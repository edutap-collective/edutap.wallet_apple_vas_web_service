"""Module for Apple Push Notification Service
see: https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns
"""

import requests

DEVELOPMENT_SERVER = "https://api.sandbox.push.apple.com"
PRODUCTION_SERVER = "https://api.push.apple.com"


async def send_notification(
    server: str,
    deviceLibraryIdentitfier: str,
):
    """see: https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns#Send-a-POST-request-to-APNs"""
    # A timeout, because there is no default one: without it a stalled connection to
    # Apple holds this call open indefinitely. Ten seconds is generous for a request
    # whose payload is an empty JSON object.
    result = requests.post(url=f"{server}/3/device/{deviceLibraryIdentitfier}", timeout=10)
    if result.status_code == 200:
        return True
