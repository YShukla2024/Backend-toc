import os
import base64
import requests
import msal
from dotenv import load_dotenv

load_dotenv()

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
APP_URL = os.getenv("APP_URL")


class EmailService:

    def __init__(self):

        self.authority = f"https://login.microsoftonline.com/{TENANT_ID}"

        self.app = msal.ConfidentialClientApplication(
            CLIENT_ID,
            authority=self.authority,
            client_credential=CLIENT_SECRET
        )

        self.scope = ["https://graph.microsoft.com/.default"]

        # base path
        BASE_DIR = os.path.dirname(os.path.abspath(__file__))

        # correct folder path
        self.generated_folder = os.path.join(BASE_DIR, "..", "storage", "generated")
        self.generated_folder = os.path.abspath(self.generated_folder)

        # create folder if not exists
        os.makedirs(self.generated_folder, exist_ok=True)
    # ---------------------------------------------------
    # Get Graph API token
    # ---------------------------------------------------
    def get_access_token(self):

        result = self.app.acquire_token_for_client(scopes=self.scope)

        if "access_token" not in result:
            raise Exception(f"Token error: {result}")

        return result["access_token"]

    # ---------------------------------------------------
    # Resolve DOCX path using version_id
    # ---------------------------------------------------
    def get_latest_generated_file(self):

        folder = self.generated_folder

        print("Looking for generated files in:", folder)

        if not os.path.exists(folder):
            raise Exception(f"Generated folder not found at {folder}")

        files = [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.endswith(".docx")
        ]

        if not files:
            raise Exception("No generated DOCX files found")

        latest_file = max(files, key=os.path.getmtime)

        print("Latest generated DOCX:", latest_file)

        return latest_file

    # ---------------------------------------------------
    # Send email with attachment
    # ---------------------------------------------------
    def send_toc_for_review(self, version_id, sme_email, toc_data, kam_email):
        print("EMAIL FUNCTION TRIGGERED")
        print("Sending email via Graph API...")
        access_token = self.get_access_token()

        # get file path automatically
        file_path = self.get_latest_generated_file()

        review_link = f"{APP_URL}?review_id={version_id}"

        with open(file_path, "rb") as f:
            file_content = base64.b64encode(f.read()).decode()

        subject = f"TOC Review Request – {toc_data.get('title')}"

        body = f"""
Hello,

An AI-generated TOC is ready for your review.

Course Title: {toc_data.get('title')}
Duration: {toc_data.get('duration')}

Please review using the link below:

{review_link}

The Word document is attached for reference.

Regards,
Synergetics AI TOC System
"""

        email_message = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "Text",
                    "content": body
                },
                "toRecipients": [
                    {
                        "emailAddress": {
                            "address": sme_email
                        }
                    }
                ],
                "attachments": [
                    {
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": os.path.basename(file_path),
                        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "contentBytes": file_content
                    }
                ]
            },
            "saveToSentItems": True
        }

        endpoint = f"https://graph.microsoft.com/v1.0/users/{kam_email}/sendMail"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        print("Attachment path:", file_path)
        print("Sender mailbox:", kam_email)

        response = requests.post(endpoint, headers=headers, json=email_message)

        print("Graph status:", response.status_code)
        print("Graph response:", response.text)

        if response.status_code != 202:
            raise Exception(f"Email failed: {response.text}")