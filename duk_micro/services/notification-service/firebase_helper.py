# services/notification-service/firebase_helper.py
import firebase_admin
from firebase_admin import credentials, messaging
import os
import json
import logging

logger = logging.getLogger(__name__)

def get_firebase_app():
    if not firebase_admin._apps:
        # Load from env var (JSON string) in production, or fallback to file
        creds_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
        if creds_json:
            cred = credentials.Certificate(json.loads(creds_json))
        else:
            cred_path = os.environ.get("FIREBASE_CREDENTIALS_PATH", "firebase_creds.json")
            if os.path.exists(cred_path):
                cred = credentials.Certificate(cred_path)
            else:
                logger.warning("Firebase credentials not found. Notifications will fail.")
                return None
        return firebase_admin.initialize_app(cred)
    return firebase_admin.get_app()

def send_multicast_message(tokens: list[str], title: str, body: str, data: dict = None):
    app = get_firebase_app()
    if not app:
        class MockResponse:
            success_count = 0
            failure_count = len(tokens)
        return MockResponse()
        
    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data=data or {},
        tokens=tokens
    )
    return messaging.send_multicast(message)
