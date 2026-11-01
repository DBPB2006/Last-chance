import requests
from django.conf import settings


class SetuClient:

    @classmethod
    def is_configured(cls) -> bool:
        return bool(
            (settings.SETU_CLIENT_ID or '').strip() and
            (settings.SETU_CLIENT_SECRET or '').strip() and
            (settings.SETU_PRODUCT_INSTANCE_ID or '').strip()
        )

    @classmethod
    def _headers(cls) -> dict:
        return {
            'Content-Type': 'application/json',
            'x-client-id': (settings.SETU_CLIENT_ID or '').strip(),
            'x-client-secret': (settings.SETU_CLIENT_SECRET or '').strip(),
            'x-product-instance-id': (settings.SETU_PRODUCT_INSTANCE_ID or '').strip(),
        }

    @classmethod
    def create_digilocker_request(cls, redirect_url: str | None = None) -> dict:
        if not cls.is_configured():
            return {
                'success': False,
                'status': 'UNCONFIGURED',
                'error': 'Setu DigiLocker sandbox credentials are not configured in the environment.',
            }

        target_url = f"{settings.SETU_BASE_URL.rstrip('/')}/api/digilocker/"
        final_redirect = (redirect_url or settings.SETU_REDIRECT_URL or '').strip()

        try:
            response = requests.post(
                target_url,
                json={'redirectUrl': final_redirect},
                headers=cls._headers(),
                timeout=25,
            )
            if response.status_code in (200, 201):
                data = response.json()
                return {
                    'success': True,
                    'status': 'INITIATED',
                    'request_id': data.get('id'),
                    'url': data.get('url'),
                    'valid_upto': data.get('validUpto'),
                }
            return {
                'success': False,
                'status': 'FAILED',
                'error': f"Setu API returned HTTP {response.status_code}: {response.text}",
            }
        except requests.RequestException as e:
            return {
                'success': False,
                'status': 'ERROR',
                'error': f"Failed to connect to Setu sandbox: {str(e)}",
            }

    @classmethod
    def get_request_status(cls, setu_request_id: str) -> dict:
        if not cls.is_configured():
            return {
                'success': False,
                'status': 'UNCONFIGURED',
                'error': 'Setu credentials not configured.',
            }

        target_url = f"{settings.SETU_BASE_URL.rstrip('/')}/api/digilocker/{setu_request_id}/status"
        try:
            response = requests.get(target_url, headers=cls._headers(), timeout=25)
            if response.status_code == 200:
                data = response.json()
                raw_status = data.get('status', '').lower()
                normalized_status = 'INITIATED'
                if raw_status == 'authenticated':
                    normalized_status = 'AUTHENTICATED'
                elif raw_status in ('expired', 'revoked'):
                    normalized_status = 'EXPIRED'
                elif raw_status in ('rejected', 'failed'):
                    normalized_status = 'FAILED'

                return {
                    'success': True,
                    'status': normalized_status,
                    'raw_status': raw_status,
                    'data': data,
                }
            return {
                'success': False,
                'status': 'FAILED',
                'error': f"Setu status lookup returned HTTP {response.status_code}: {response.text}",
            }
        except requests.RequestException as e:
            return {
                'success': False,
                'status': 'ERROR',
                'error': f"Failed to contact Setu API: {str(e)}",
            }

    @classmethod
    def fetch_aadhaar_data(cls, setu_request_id: str) -> dict:
        if not cls.is_configured():
            return {
                'success': False,
                'error': 'Setu credentials not configured.',
            }

        target_url = f"{settings.SETU_BASE_URL.rstrip('/')}/api/digilocker/{setu_request_id}/aadhaar"
        try:
            response = requests.get(target_url, headers=cls._headers(), timeout=25)
            if response.status_code == 200:
                data = response.json()
                # Setu may return demographic details nested under 'aadhaar', 'data', or top-level
                aadhaar_data = data.get('aadhaar') or data.get('data') or data
                if isinstance(aadhaar_data, dict) and isinstance(aadhaar_data.get('aadhaar'), dict):
                    aadhaar_data = aadhaar_data['aadhaar']

                legal_name = ''
                if isinstance(aadhaar_data, dict):
                    legal_name = (
                        aadhaar_data.get('name') or
                        aadhaar_data.get('fullName') or
                        aadhaar_data.get('full_name') or
                        (aadhaar_data.get('user', {}) or {}).get('name') or
                        ''
                    )
                if not legal_name and isinstance(data, dict):
                    legal_name = (
                        data.get('name') or
                        data.get('fullName') or
                        (data.get('digilockerUserDetails', {}) or {}).get('name') or
                        ''
                    )

                return {
                    'success': True,
                    'verified_legal_name': str(legal_name).strip(),
                    'raw_response': data,
                }
            return {
                'success': False,
                'error': f"Setu Aadhaar fetch returned HTTP {response.status_code}: {response.text}",
            }
        except requests.RequestException as e:
            return {
                'success': False,
                'error': f"Failed to fetch Aadhaar data: {str(e)}",
            }
