import os
import requests

def handler(request, response):
    # Load environment variables
    api_url = os.getenv("WHAPI_URL")          # e.g. "https://gate.whapi.cloud/messages/text"
    api_token = os.getenv("WHAPI_TOKEN")      # Your Whapi token
    to_number = os.getenv("WHAPI_TO")         # The phone number to send to

    if not api_url or not api_token or not to_number:
        return response.status(500).json({
            "error": "Missing environment variables"
        })

    # Message payload
    payload = {
        "to": to_number,
        "body": "Hello from Vercel Python — keeping WhatsApp session alive!"
    }

    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(api_url, json=payload, headers=headers)
        return response.status(r.status_code).json(r.json())
    except Exception as e:
        return response.status(500).json({
            "error": str(e)
        })
