class AuthService:

    SME_EMAILS = [
        "sme1@synergetics-india.com",
        "sme2@synergetics-india.com",
    ]

    def validate_domain(self, email):
        if not email:
            return False

        email = email.lower().strip()

        return email.endswith((
            "@synergetics-india.com",
            "@synergetics-consulting.com"
        ))

    def get_role(self, email):
        if email in self.SME_EMAILS:
            return "SME"
        return "KAM"

    def get_sme_list(self):
        return self.SME_EMAILS