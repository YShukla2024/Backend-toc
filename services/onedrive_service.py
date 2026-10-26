import os
import token
import requests
import msal
from dotenv import load_dotenv

load_dotenv()
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")


class OneDriveService:

    def __init__(self):
        authority = f"https://login.microsoftonline.com/{TENANT_ID}"

        self.app = msal.ConfidentialClientApplication(
            CLIENT_ID,
            authority=authority,
            client_credential=CLIENT_SECRET
        )

        self.scope = ["https://graph.microsoft.com/.default"]

    def get_token(self):

        result = self.app.acquire_token_for_client(scopes=self.scope)

        if "access_token" not in result:
            raise Exception(f"Token error: {result}")

        return result["access_token"]

    # ----------------------------------------
    # List files inside OneDrive folder
    # ----------------------------------------
    def list_files(self, user_email, folder_path):

        token = self.get_token()

        headers = {   # ✅ MOVE THIS HERE (before if/else)
            "Authorization": f"Bearer {token}"
        }

        if folder_path:
            url = f"https://graph.microsoft.com/v1.0/users/{user_email}/drive/root:/{folder_path}:/children"
        else:
            url = f"https://graph.microsoft.com/v1.0/users/{user_email}/drive/root/children"

        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            raise Exception(response.text)

        return response.json()["value"]
    # ----------------------------------------
    # List all files recursively
    # ----------------------------------------
    def list_all_files_recursive(self, user_email, folder_path):

        all_files = []

        items = self.list_files(user_email, folder_path)

        for item in items:

            name = item["name"]

            if name.lower().endswith(".pdf"):
                print("📄 PDF detected:", name)

            # If folder → go inside
            if "folder" in item:

                sub_path = f"{folder_path}/{name}"

                print("Entering folder:", sub_path)

                all_files.extend(
                    self.list_all_files_recursive(user_email, sub_path)
                )

            else:
                # file
                if name.lower().endswith((".docx", ".pdf")):
                    all_files.append(item)

        return all_files
    # ----------------------------------------
    # Get file content (in memory)
    # ----------------------------------------
    def get_file_bytes(self, download_url):

        response = requests.get(download_url)

        if response.status_code != 200:
            raise Exception("Failed to download file")

        return response.content