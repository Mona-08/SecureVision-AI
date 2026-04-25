import os
from flask import Flask, request, jsonify
from flask_cors import CORS
from google import genai
from google.genai import types
import PIL.Image
import requests
from io import BytesIO

app = Flask(__name__)
CORS(app) # Allows your website to talk to this API

# Setup Client

client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

import time
@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.get_json(silent=True)
   
    if not data or 'url' not in data:
        print("❌ Error: No URL found in the request body")
        return jsonify({"error": "No URL provided"}), 400

    source = data.get('url', '').strip()
    print(f"🔄 Received request to analyze: {source}")
   
    if not source:
        return jsonify({"error": "No URL or text provided"}), 400

    cleanup_file_path = None
    cleanup_gemini_file = None
    try:
        contents_for_gemini = []

        if source.startswith('http://') or source.startswith('https://'):
            from urllib.parse import urlparse
            domain = urlparse(source).netloc
            domain_context = (
                f"\n\nSource Domain: {domain}. "
                "Consider whether this domain is a known official, reputable sports media domain "
                "(like espn.com, nba.com, etc.) or a potentially suspicious/unauthorized domain "
                "when determining the integrity verdict and authenticity. "
                "CRITICAL: Use the Google Search tool to verify if any specific sports news, trades, "
                "or events depicted in this media actually occurred recently. Cross-reference claims with live search results."
            )

            # 1. Fetch content with timeout and headers
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(source, headers=headers, timeout=30)
            response.raise_for_status()
           
            content_type = response.headers.get('Content-Type', '')

            if 'image' in content_type:
                media_content = PIL.Image.open(BytesIO(response.content))
                prompt = (
                    "Analyze this image for sports media integrity. "
                    "Return JSON with: is_official (bool), detected_logos (list), "
                    "integrity_verdict (string), reasoning (string)."
                ) + domain_context
                contents_for_gemini = [prompt, media_content]

            elif 'video' in content_type:
                import tempfile
                import os
               
                # Save video to temporary file
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_video:
                    temp_video.write(response.content)
                    cleanup_file_path = temp_video.name
               
                # Upload to Gemini File API
                print("⏳ Uploading video to Gemini...")
                video_file = client.files.upload(file=cleanup_file_path)
                cleanup_gemini_file = video_file.name
               
                # Wait for processing
                print("⏳ Waiting for video processing", end="")
                while video_file.state.name == "PROCESSING":
                    print(".", end="", flush=True)
                    time.sleep(2)
                    video_file = client.files.get(name=video_file.name)
                print(" Done!")
               
                prompt = (
                    "Analyze this video for sports media integrity. "
                    "Return JSON with: is_official (bool), detected_logos (list), "
                    "integrity_verdict (string), reasoning (string)."
                ) + domain_context
                contents_for_gemini = [prompt, video_file]

            else:
                import re
                text_content = response.text
                # remove script and style tags
                text_content = re.sub(r'<(script|style).*?>.*?</\1>', '', text_content, flags=re.DOTALL | re.IGNORECASE)
                # remove html tags
                text_content = re.sub(r'<.*?>', ' ', text_content, flags=re.DOTALL)
                text_content = re.sub(r'\s+', ' ', text_content).strip()
               
                media_content = f"Website Text Content:\n{text_content[:100000]}"
                prompt = (
                    "Analyze this sports website text content for media integrity, authenticity, and official associations. "
                    "Return JSON with: is_official (bool), detected_logos (list of mentioned official entities/brands/logos), "
                    "integrity_verdict (string), reasoning (string)."
                ) + domain_context
                contents_for_gemini = [prompt, media_content]

        else:
            # Handle plain text input (Sports News snippet)
            domain_context = (
                "\n\nSource: Raw Text Input provided by the user. "
                "CRITICAL: Use the Google Search tool to verify if the sports news, trades, "
                "or events mentioned in this text actually occurred recently. Cross-reference claims with live search results."
            )
            media_content = f"Sports News Text:\n{source}"
            prompt = (
                "Analyze this sports news text for media integrity, authenticity, and factual accuracy. "
                "Return JSON with: is_official (bool - is this confirmed real news by official sources?), detected_logos (list of mentioned official entities/brands/teams), "
                "integrity_verdict (string), reasoning (string)."
            ) + domain_context
            contents_for_gemini = [prompt, media_content]

        # 3. Retry Loop (The "Contest Winner" Logic)
        result_text = None
        for attempt in range(3):
            try:
                # Switching to 2.5-flash for maximum stability during the demo
                result = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=contents_for_gemini,
                    config=types.GenerateContentConfig(
                        tools=[{"google_search": {}}]
                    )
                )
                print("✅ Gemini analysis successful!")
                result_text = result.text
                break
            except Exception as e:
                # If the error is 503 (Server Busy) or 429, wait and try again
                if "503" in str(e) or "504" in str(e) or "429" in str(e):
                    wait_time = (attempt + 1) * 2
                    print(f"⚠️ Server busy, retrying in {wait_time}s... (Attempt {attempt+1}/3)")
                    time.sleep(wait_time)
                    continue
                else:
                    # If it's a different error (like a bad prompt), stop and report it
                    raise e
                   
        if not result_text:
            raise Exception("Failed to generate content after 3 attempts.")

    except Exception as e:
        print(f"❌ Analysis Error: {str(e)}")
        return jsonify({"error": str(e)}), 500
       
    finally:
        # Cleanup temporary files
        if cleanup_file_path:
            import os
            try:
                os.remove(cleanup_file_path)
            except: pass
        if cleanup_gemini_file:
            try:
                client.files.delete(name=cleanup_gemini_file)
            except: pass

    # Clean the result text and parse JSON safely
    import json
    try:
        clean_text = result_text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
           
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
           
        parsed_json = json.loads(clean_text.strip())
       
        # Ensure fallback defaults if Gemini changes keys
        if "integrity_verdict" not in parsed_json:
            parsed_json["integrity_verdict"] = parsed_json.get("verdict", "Unknown Verdict")
           
        return jsonify(parsed_json)
       
    except Exception as e:
        print(f"❌ Failed to parse Gemini response as JSON: {result_text}")
        return jsonify({
            "is_official": False,
            "detected_logos": [],
            "integrity_verdict": "Error Parsing Response",
            "reasoning": f"Failed to parse AI response. Raw output: {result_text}"
        })
   

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
