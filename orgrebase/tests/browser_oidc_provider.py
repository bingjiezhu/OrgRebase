"""A real HTTPS OIDC test provider with signed tokens and single-use PKCE codes."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def tls_files(root: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ip_address("127.0.0.1"))]), critical=False)
                   .sign(key, hashes.SHA256()))
    cert, private = root / "tls-cert.pem", root / "tls-key.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    private.chmod(0o600)
    return cert, private


class LocalOIDCProvider:
    def __init__(self, certificate: Path, private: Path) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.client_id, self.client_secret = "browser-client", secrets.token_urlsafe(32)
        self.audience, self.subject, self.sid = "resource-api", "operator", secrets.token_urlsafe(20)
        self.redirect_uri = ""
        self.access_overrides: dict = {}
        self.id_overrides: dict = {}
        self.metadata_overrides: dict = {}
        self.tokens: list[str] = []
        self.codes: dict[str, dict] = {}
        self.token_requests = 0
        self.wrong_signing_key = False
        self.redirect_jwks = False
        self.redirect_token = False
        self.unexpected_requests = 0
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def json(self, body: dict, status: int = 200):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                path = urlsplit(self.path)
                if path.path == "/.well-known/openid-configuration":
                    self.json({"issuer": provider.issuer, "authorization_endpoint": provider.issuer + "/authorize",
                               "token_endpoint": provider.issuer + "/token", "jwks_uri": provider.issuer + "/jwks",
                               "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"],
                               "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                               "authorization_response_iss_parameter_supported": True, **provider.metadata_overrides})
                elif path.path == "/jwks":
                    if provider.redirect_jwks:
                        self.send_response(302)
                        self.send_header("Location", provider.issuer + "/unexpected")
                        self.end_headers()
                        return
                    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(provider.key.public_key()))
                    self.json({"keys": [{**jwk, "kid": "oidc-key", "use": "sig", "alg": "RS256"}]})
                elif path.path == "/authorize":
                    params = {name: values[0] for name, values in parse_qs(path.query).items()}
                    if (params.get("client_id") != provider.client_id or params.get("redirect_uri") != provider.redirect_uri
                            or params.get("response_type") != "code" or params.get("code_challenge_method") != "S256"
                            or not {"state", "nonce", "code_challenge"}.issubset(params)):
                        self.json({"error": "invalid_request"}, 400)
                        return
                    code = secrets.token_urlsafe(32)
                    provider.codes[code] = params
                    self.send_response(302)
                    self.send_header("Location", provider.redirect_uri + "?" + urlencode({
                        "code": code, "state": params["state"], "iss": provider.issuer,
                    }))
                    self.end_headers()
                else:
                    provider.unexpected_requests += 1
                    self.json({"error": "not_found"}, 404)

            def do_POST(self):
                provider.token_requests += 1
                if provider.redirect_token:
                    self.send_response(302)
                    self.send_header("Location", provider.issuer + "/unexpected")
                    self.end_headers()
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                params = {name: values[0] for name, values in parse_qs(body.decode()).items()}
                expected = "Basic " + base64.b64encode(f"{provider.client_id}:{provider.client_secret}".encode()).decode()
                code = provider.codes.pop(params.get("code", ""), None)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(params.get("code_verifier", "").encode()).digest()).rstrip(b"=").decode()
                if (self.path != "/token" or self.headers.get("Authorization") != expected
                        or code is None or params.get("grant_type") != "authorization_code"
                        or params.get("redirect_uri") != provider.redirect_uri or challenge != code["code_challenge"]):
                    self.json({"error": "invalid_grant"}, 400)
                    return
                now = int(time.time())
                signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048) if provider.wrong_signing_key else provider.key
                access = jwt.encode({"iss": provider.issuer, "aud": provider.audience, "sub": provider.subject,
                                     "iat": now, "exp": now + 180, "token_use": "access", **provider.access_overrides},
                                    signing_key, algorithm="RS256", headers={"kid": "oidc-key"})
                access_hash = base64.urlsafe_b64encode(hashlib.sha256(access.encode()).digest()[:16]).rstrip(b"=").decode()
                identity = jwt.encode({"iss": provider.issuer, "aud": provider.client_id, "sub": provider.subject,
                                       "iat": now, "exp": now + 180, "nonce": code["nonce"], "sid": provider.sid,
                                       "at_hash": access_hash, **provider.id_overrides},
                                      signing_key, algorithm="RS256", headers={"kid": "oidc-key"})
                provider.tokens.extend([access, identity])
                self.json({"access_token": access, "id_token": identity, "token_type": "Bearer", "expires_in": 180})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, private)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.issuer = f"https://localhost:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def logout_token(self, **overrides) -> str:
        return jwt.encode({"iss": self.issuer, "aud": self.client_id, "iat": int(time.time()),
                           "jti": secrets.token_urlsafe(24), "sub": self.subject, "sid": self.sid,
                           "events": {"http://schemas.openid.net/event/backchannel-logout": {}}, **overrides},
                          self.key, algorithm="RS256", headers={"kid": "oidc-key"})

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
