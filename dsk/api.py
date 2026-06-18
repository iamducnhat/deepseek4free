from curl_cffi import requests, CurlMime
from typing import Optional, Dict, Any, Generator, Literal, List
import json
import mimetypes
from .pow import DeepSeekPOW
import sys
from pathlib import Path
import subprocess
import time

ThinkingMode = Literal['detailed', 'simple', 'disabled']
SearchMode = Literal['enabled', 'disabled']

class DeepSeekError(Exception):
    """Base exception for all DeepSeek API errors"""
    pass

class AuthenticationError(DeepSeekError):
    """Raised when authentication fails"""
    pass

class RateLimitError(DeepSeekError):
    """Raised when API rate limit is exceeded"""
    pass

class NetworkError(DeepSeekError):
    """Raised when network communication fails"""
    pass

class CloudflareError(DeepSeekError):
    """Raised when Cloudflare blocks the request"""
    pass

class APIError(DeepSeekError):
    """Raised when API returns an error response"""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code

class DeepSeekAPI:
    BASE_URL = "https://chat.deepseek.com/api/v0"

    def __init__(self, auth_token: str):
        if not auth_token or not isinstance(auth_token, str):
            raise AuthenticationError("Invalid auth token provided")

        try:
            self.impersonate = "chrome120"
        except Exception:
            self.impersonate = "chrome120"

        self.auth_token = auth_token
        self.pow_solver = DeepSeekPOW()

        self.cookies = {}
        try:
            cookies_path = Path.home() / '.canvas_sync_vault' / 'cookies.json'
            with open(cookies_path, 'r') as f:
                cookie_data = json.load(f)
                self.cookies = cookie_data.get('cookies', {})
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"\033[93mWarning: Could not load cookies from {cookies_path}: {e}\033[0m", file=sys.stderr)
            self.cookies = {}

    def _get_headers(self, pow_response: Optional[str] = None) -> Dict[str, str]:
        headers = {
            'accept': '*/*',
            'accept-language': 'en,fr-FR;q=0.9,fr;q=0.8,es-ES;q=0.7,es;q=0.6,en-US;q=0.5,am;q=0.4,de;q=0.3',
            'authorization': f'Bearer {self.auth_token}',
            'content-type': 'application/json',
            'origin': 'https://chat.deepseek.com',
            'referer': 'https://chat.deepseek.com/',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36',
            'x-app-version': '20241129.1',
            'x-client-locale': 'en_US',
            'x-client-platform': 'web',
            'x-client-version': '1.0.0-always',
        }

        if pow_response:
            headers['x-ds-pow-response'] = pow_response

        return headers

    def _refresh_cookies(self) -> None:
        """Run the cookie refresh script and reload cookies"""
        try:
            # Get path to bypass.py
            script_path = Path(__file__).parent / 'bypass.py'

            # Run the script
            subprocess.run([sys.executable, script_path], check=True)

            # Wait briefly for cookies file to be written
            time.sleep(2)

            # Reload cookies
            cookies_path = Path.home() / '.canvas_sync_vault' / 'cookies.json'
            with open(cookies_path, 'r') as f:
                cookie_data = json.load(f)
                self.cookies = cookie_data.get('cookies', {})

        except Exception as e:
            print(f"\033[93mWarning: Failed to refresh cookies: {e}\033[0m", file=sys.stderr)

    def _make_request(self, method: str, endpoint: str, json_data: Dict[str, Any], pow_required: bool = False) -> Any:
        url = f"{self.BASE_URL}{endpoint}"

        retry_count = 0
        max_retries = 2

        while retry_count < max_retries:
            try:
                headers = self._get_headers()
                if pow_required:
                    challenge = self._get_pow_challenge()
                    pow_response = self.pow_solver.solve_challenge(challenge)
                    headers = self._get_headers(pow_response)

                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json_data,
                    cookies=self.cookies,
                    impersonate='chrome120',
                    timeout=None
                )

                # Check if we hit Cloudflare protection
                if "<!DOCTYPE html>" in response.text and "Just a moment" in response.text:
                    print("\033[93mWarning: Cloudflare protection detected. Bypassing...\033[0m", file=sys.stderr)
                    if retry_count < max_retries - 1:
                        self._refresh_cookies()  # Refresh cookies
                        retry_count += 1
                        continue

                # Handle other response codes
                if response.status_code == 401:
                    raise AuthenticationError("Invalid or expired authentication token")
                elif response.status_code == 429:
                    raise RateLimitError("API rate limit exceeded")
                elif response.status_code >= 500:
                    raise APIError(f"Server error occurred: {response.text}", response.status_code)
                elif response.status_code != 200:
                    raise APIError(f"API request failed: {response.text}", response.status_code)

                return response.json()

            except requests.exceptions.RequestException as e:
                raise NetworkError(f"Network error occurred: {str(e)}")
            except json.JSONDecodeError:
                raise APIError("Invalid JSON response from server")

        raise APIError("Failed to bypass Cloudflare protection after multiple attempts")

    def _make_request_upload_file(self, endpoint: str, file_path: str) -> Any:
        url = f"{self.BASE_URL}{endpoint}"
        retry_count = 0
        max_retries = 2

        while retry_count < max_retries:
            try:
                # Need to solve POW challenge specifically for upload_file
                challenge_res = self._make_request(
                    'POST',
                    '/chat/create_pow_challenge',
                    {'target_path': '/api/v0/file/upload_file'}
                )
                challenge = challenge_res['data']['biz_data']['challenge']
                pow_response = self.pow_solver.solve_challenge(challenge)
                headers = self._get_headers(pow_response)
                
                # Remove content-type so curl_cffi can generate the correct multipart boundary
                if 'content-type' in headers:
                    del headers['content-type']

                with open(file_path, "rb") as f:
                    data = f.read()

                mime_type, _ = mimetypes.guess_type(file_path)
                if not mime_type:
                    mime_type = 'application/octet-stream'

                mp = CurlMime()
                mp.addpart(
                    name="file",
                    content_type=mime_type,
                    filename=Path(file_path).name,
                    data=data,
                )

                response = requests.request(
                    method='POST',
                    url=url,
                    headers=headers,
                    multipart=mp,
                    cookies=self.cookies,
                    impersonate=self.impersonate,
                    timeout=None
                )

                if "<!DOCTYPE html>" in response.text and "Just a moment" in response.text:
                    print("\033[93mWarning: Cloudflare protection detected during upload. Bypassing...\033[0m", file=sys.stderr)
                    if retry_count < max_retries - 1:
                        self._refresh_cookies()
                        retry_count += 1
                        continue

                if response.status_code == 401:
                    raise AuthenticationError("Invalid or expired authentication token")
                elif response.status_code == 429:
                    raise RateLimitError("API rate limit exceeded")
                elif response.status_code >= 500:
                    raise APIError(f"Server error occurred: {response.text}", response.status_code)
                elif response.status_code != 200:
                    raise APIError(f"API request failed: {response.text}", response.status_code)

                return response.json()

            except requests.exceptions.RequestException as e:
                raise NetworkError(f"Network error occurred: {str(e)}")
            except json.JSONDecodeError:
                raise APIError("Invalid JSON response from server")
            except KeyError:
                raise APIError("Invalid challenge response format from server")

        raise APIError("Failed to bypass Cloudflare protection during upload")

    def _get_pow_challenge(self) -> Dict[str, Any]:
        try:
            response = self._make_request(
                'POST',
                '/chat/create_pow_challenge',
                {'target_path': '/api/v0/chat/completion'}
            )
            return response['data']['biz_data']['challenge']
        except KeyError:
            raise APIError("Invalid challenge response format from server")

    def upload_file(self, file_path: str) -> str:
        """Uploads a file to DeepSeek and returns the file_id"""
        try:
            response = self._make_request_upload_file('/file/upload_file', file_path)
            file_id = response['data']['biz_data']['id']
            
            # Poll status until SUCCESS
            for _ in range(30): # Wait up to 30 seconds
                time.sleep(1)
                headers = self._get_headers()
                r = requests.get(
                    f"{self.BASE_URL}/file/fetch_files?file_ids={file_id}",
                    headers=headers,
                    cookies=self.cookies,
                    impersonate=self.impersonate
                )
                if r.status_code == 200:
                    try:
                        res_data = r.json()
                        files = res_data.get('data', {}).get('biz_data', {}).get('files', [])
                        if files and files[0].get('status') == 'SUCCESS':
                            return file_id
                        elif files and files[0].get('status') == 'FAILED':
                            raise APIError("Backend failed to process the uploaded file")
                    except Exception:
                        pass
            return file_id
        except KeyError:
            raise APIError('Invalid upload file response format from server')

    def create_chat_session(self) -> str:
        """Creates a new chat session and returns the session ID"""
        try:
            response = self._make_request(
                'POST',
                '/chat_session/create',
                {'character_id': None}
            )
            return response['data']['biz_data']['id']
        except KeyError:
            raise APIError("Invalid session creation response format from server")

    def chat_completion(self,
                    chat_session_id: str,
                    prompt: str,
                    ref_file_ids: Optional[List[str]] = None,
                    parent_message_id: Optional[str] = None,
                    thinking_enabled: bool = True,
                    search_enabled: bool = False) -> Generator[Dict[str, Any], None, None]:
        """
        Send a message and get streaming response

        Args:
            chat_session_id (str): The ID of the chat session
            prompt (str): The message to send
            ref_file_ids (Optional[List[str]]): List of file IDs to reference
            parent_message_id (Optional[str]): ID of the parent message for threading
            thinking_enabled (bool): Whether to show the thinking process
            search_enabled (bool): Whether to enable web search for up-to-date information

        Returns:
            Generator[Dict[str, Any], None, None]: Yields message chunks with content and type

        Raises:
            AuthenticationError: If the authentication token is invalid
            RateLimitError: If the API rate limit is exceeded
            NetworkError: If a network error occurs
            APIError: If any other API error occurs
        """
        if not prompt or not isinstance(prompt, str):
            raise ValueError("Prompt must be a non-empty string")
        if not chat_session_id or not isinstance(chat_session_id, str):
            raise ValueError("Chat session ID must be a non-empty string")

        json_data = {
            'chat_session_id': chat_session_id,
            'parent_message_id': parent_message_id,
            'prompt': prompt,
            'ref_file_ids': ref_file_ids or [],
            'thinking_enabled': thinking_enabled,
            'search_enabled': search_enabled,
        }

        try:
            headers = self._get_headers(
                pow_response=self.pow_solver.solve_challenge(
                    self._get_pow_challenge()
                )
            )

            response = requests.post(
                f"{self.BASE_URL}/chat/completion",
                headers=headers,
                json=json_data,
                cookies=self.cookies,  # Add cookies
                impersonate=self.impersonate,
                stream=True,
                timeout=None
            )

            if 'text/html' in response.headers.get('content-type', ''):
                print("\033[93mWarning: Cloudflare protection detected during chat. Bypassing...\033[0m", file=sys.stderr)
                self._refresh_cookies()
                yield from self.chat_completion(chat_session_id, prompt, parent_message_id, thinking_enabled, search_enabled)
                return

            if response.status_code != 200:
                error_text = next(response.iter_lines(), b'').decode('utf-8', 'ignore')
                if response.status_code == 401:
                    raise AuthenticationError("Invalid or expired authentication token")
                elif response.status_code == 429:
                    raise RateLimitError("API rate limit exceeded")
                else:
                    raise APIError(f"API request failed: {error_text}", response.status_code)

            for chunk in response.iter_lines():
                if not chunk:
                    continue
                # Handle error JSON returned instead of SSE stream
                if chunk.startswith(b'{"code":'):
                    try:
                        err_data = json.loads(chunk)
                        biz_msg = err_data.get('data', {}).get('biz_msg', 'Unknown backend error')
                        raise APIError(f"Backend rejected request: {biz_msg}")
                    except json.JSONDecodeError:
                        pass

                try:
                    parsed = self._parse_chunk(chunk)
                    if parsed:
                        yield parsed
                        if parsed.get('finish_reason') == 'stop':
                            break
                except Exception as e:
                    raise APIError(f"Error parsing response chunk: {str(e)}")

        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Network error occurred during streaming: {str(e)}")

    def _parse_chunk(self, chunk: bytes) -> Optional[Dict[str, Any]]:
        """Parse a SSE chunk from the API response"""
        if not chunk:
            return None

        try:
            if chunk.startswith(b'data: '):
                chunk_str = chunk[6:].decode('utf-8', 'ignore').strip()
                if not chunk_str or chunk_str == '{}':
                    return None
                data = json.loads(chunk_str)

                # Old format
                if 'choices' in data and data['choices']:
                    choice = data['choices'][0]
                    if 'delta' in choice:
                        delta = choice['delta']
                        return {
                            'content': delta.get('content', ''),
                            'type': delta.get('type', ''),
                            'finish_reason': choice.get('finish_reason')
                        }
                
                # New format
                if 'v' in data:
                    p = data.get('p')
                    if p == 'response/thinking_content':
                        self._current_type = 'thinking'
                    elif p == 'response/content':
                        self._current_type = 'text'
                    elif p == 'response/status' and data['v'] == 'FINISHED':
                        return {'content': '', 'type': 'text', 'finish_reason': 'stop'}
                    
                    if not hasattr(self, '_current_type'):
                        self._current_type = 'thinking'
                        
                    if isinstance(data['v'], str):
                        return {
                            'content': data['v'],
                            'type': getattr(self, '_current_type', 'text'),
                            'finish_reason': None
                        }
                        
        except json.JSONDecodeError:
            pass # ignore invalid json
        except Exception as e:
            raise APIError(f"Error parsing chunk: {str(e)}")

        return None
