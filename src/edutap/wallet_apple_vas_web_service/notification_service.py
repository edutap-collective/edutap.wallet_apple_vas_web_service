"""
Module for Apple Push Notification Service
see: https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns
"""
import requests


DEVELOPMENT_SERVER = "https://api.sandbox.push.apple.com"
PRODUCTION_SERVER = "https://api.push.apple.com"


async def send_notification(
    server: str,
    deviceLibraryIdentitfier: str,
):
    """
    see: https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns#Send-a-POST-request-to-APNs
    """

    result = requests.post(url=f"{server}/3/device/{deviceLibraryIdentitfier}")
    if result.status_code == 200:
        return True
