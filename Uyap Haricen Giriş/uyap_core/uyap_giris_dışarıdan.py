#!/usr/bin/env python3
"""
UYAP Login Replication Script (E-Signature)
-------------------------------------------
This script programmatically replicates the UYAP login flow using a local e-imza (e-signature) client
and standard HTTP requests via the Python `requests` library.

Flow:
1. Initialize a requests session and retrieve UYAP's landing page to set up F5 load-balancer cookies.
2. Query the local e-signature client (ArkSigner / UYAP e-imza) for available certificates on port 5975.
3. Allow the user to select a certificate if multiple are found, or automatically select the first/default one.
4. Sign the standard UYAP base64 authentication string using the selected certificate and user PIN.
5. Submit the signature and transaction ID (txId) to UYAP's signature endpoint (web1.uyap).
6. Confirm the login on UYAP (login.uyap).
7. Verify successful login by querying the post-login endpoint (get_avukat_id.ajx) to retrieve the Lawyer ID.
8. Output the active session cookies (in console and optional JSON file).

Usage:
    python uyap_giris_taklit.py [--pin PIN] [--cert-id ID] [--no-verify] [--save-cookies PATH]
"""

import os
import sys
import time
import argparse
import requests
import urllib3

# Keep console output safe on Windows (Turkish characters / pipes)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Suppress SSL certificate verification warnings if disabled by user
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Default static base64 content requested by UYAP for login signature
STATIC_AUTH_CONTENT = (
    "QWRhbGV0IEJha2FubMSxxJ/EsSBCaWxnaSDEsMWfbGVtIEdlbmVsIE3DvGTDvHJsw7zEn8O8IHRhcmFmxLFuZ"
    "mFuIGdlbGnFn3RpcmlsZW4gdXlndWxhbWF5YSBlLWltemEgZ2lyacWfIHlhcG1hayBpc3RpeW9ydW0u"
)


def initialize_session(verify_ssl=True):
    """Initializes a requests.Session and performs a GET request to UYAP landing page
    to retrieve F5 Load Balancer and initial tracking cookies.
    """
    print("[*] Step 1: Initializing UYAP Session...")
    session = requests.Session()
    
    # Configure session headers to mimic a real web browser
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Connection": "keep-alive"
    })
    
    url = "https://avukat.uyap.gov.tr/giris"
    try:
        response = session.get(url, verify=verify_ssl, timeout=15)
        print(f"[+] Landing page retrieved. Status code: {response.status_code}")
        return session
    except requests.exceptions.SSLError as ssl_err:
        print(f"[!] SSL verification failed: {ssl_err}")
        print("[!] Tip: If your Python environment lacks Turkish government root certificates (Kamusm),")
        print("[!] you may run the script with the '--no-verify' argument to bypass SSL checks.")
        sys.exit(1)
    except Exception as e:
        print(f"[!] Failed to connect to UYAP: {e}")
        sys.exit(1)


def get_certificates():
    """Queries the local e-signature client on port 5975 for certificates."""
    print("[*] Step 2: Querying local e-imza client for certificates...")
    url = "http://localhost:5975/api/v1/signature/getCertificates"
    
    try:
        response = requests.get(url, timeout=10)
    except requests.exceptions.ConnectionError:
        print("[!] Connection error: Could not reach the local e-imza client on port 5975.")
        print("[!] Make sure ArkSigner or UYAP e-signature client is open and running.")
        sys.exit(1)
    except Exception as e:
        print(f"[!] Unexpected error querying local client: {e}")
        sys.exit(1)

    if response.status_code != 200:
        print(f"[!] Local client returned unexpected HTTP status {response.status_code}.")
        sys.exit(1)

    try:
        res_json = response.json()
    except ValueError:
        print("[!] Invalid JSON returned by the local e-signature client.")
        sys.exit(1)

    metadata = res_json.get("metadata", {})
    if metadata.get("STATUS") != "SUCCESS":
        print(f"[!] Local client reported an error: {metadata.get('MESSAGE', 'Unknown error')}")
        sys.exit(1)

    # Extract certificates from active terminals/readers
    certificates = []
    for terminal_data in res_json.get("data", []):
        terminal_name = terminal_data.get("terminal", "Unknown Reader")
        is_ready = terminal_data.get("ready", False)
        
        if not is_ready:
            print(f"[!] Reader found but not ready: {terminal_name}. Please insert your smart card.")
            continue
            
        for cert in terminal_data.get("certificates", []):
            cert["_terminal"] = terminal_name
            certificates.append(cert)

    if not certificates:
        print("[!] No active certificates detected.")
        print("[!] Please verify that your smart card reader is connected and card is inserted.")
        sys.exit(1)

    return certificates


def select_certificate(certs, auto_cert_id=None):
    """Allows user to select a certificate or uses the CLI argument/default one."""
    if auto_cert_id:
        for cert in certs:
            if cert.get("certificateId") == auto_cert_id:
                print(f"[+] Automatically selected certificate ID: {auto_cert_id} ({cert.get('subject')})")
                return cert
        print(f"[!] Certificate with ID '{auto_cert_id}' not found. Falling back to selection.")

    if len(certs) == 1:
        selected = certs[0]
        print(f"[+] Found one certificate. Subject: {selected.get('subject')} (Terminal: {selected.get('_terminal')})")
        return selected

    print("\nAvailable Certificates:")
    for idx, cert in enumerate(certs):
        print(f"  [{idx}] Subject  : {cert.get('subject')}")
        print(f"      Issuer   : {cert.get('issuer')}")
        print(f"      Terminal : {cert.get('_terminal')}")
        print(f"      ID       : {cert.get('certificateId')}")
        print(f"      Valid    : {cert.get('notBefore')} to {cert.get('notAfter')}")
        print("-" * 50)

    while True:
        try:
            choice = input(f"\nSelect a certificate [0-{len(certs)-1}] (default 0): ").strip()
            if not choice:
                return certs[0]
            choice_idx = int(choice)
            if 0 <= choice_idx < len(certs):
                return certs[choice_idx]
            print(f"[!] Choice must be between 0 and {len(certs)-1}.")
        except ValueError:
            print("[!] Invalid input. Please enter a valid number.")


def sign_payload(certificate_id, pin):
    """Sends the signing request containing the certificateId, PIN, and static base64 content."""
    print("[*] Step 3: Sending signing request to local client...")
    url = "http://localhost:5975/api/v1/signature/sign"
    
    payload = {
        "certificateId": certificate_id,
        "password": pin,
        "content": STATIC_AUTH_CONTENT
    }
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
    except Exception as e:
        print(f"[!] Error occurred during signing: {e}")
        sys.exit(1)

    if response.status_code != 200:
        print(f"[!] Sign request failed with status code {response.status_code}.")
        sys.exit(1)

    try:
        res_json = response.json()
    except ValueError:
        print("[!] Invalid response JSON from sign service.")
        sys.exit(1)

    metadata = res_json.get("metadata", {})
    if metadata.get("STATUS") != "SUCCESS":
        print(f"[!] Signing failed: {metadata.get('MESSAGE', 'Unknown error')}")
        print("[!] Please make sure your PIN is correct and the card is not locked.")
        sys.exit(1)

    data = res_json.get("data", {})
    signed_data = data.get("signedData")
    tx_id = data.get("txId")

    if not signed_data or not tx_id:
        print("[!] Response did not contain 'signedData' or 'txId'.")
        sys.exit(1)

    print("[+] Payload successfully signed.")
    return signed_data, tx_id


def submit_signature(session, signed_data, tx_id, verify_ssl=True):
    """Submits the signed payload and transaction ID to UYAP (web1.uyap)."""
    print("[*] Step 4: Submitting signature to UYAP (web1.uyap)...")
    url = "https://avukat.uyap.gov.tr/web1.uyap"
    
    params = {
        "signature": signed_data,
        "txId": tx_id
    }
    
    # Update referer and headers to match the flow
    session.headers.update({
        "Referer": "https://avukat.uyap.gov.tr/giris",
        "Accept": "application/json, text/plain, */*"
    })

    try:
        response = session.get(url, params=params, verify=verify_ssl, timeout=20)
    except Exception as e:
        print(f"[!] Failed to submit signature: {e}")
        sys.exit(1)

    if response.status_code != 200:
        print(f"[!] UYAP returned error status {response.status_code} on signature submission.")
        sys.exit(1)

    try:
        res_json = response.json()
        if not res_json.get("success"):
            print(f"[!] UYAP reported signature validation failure: {res_json}")
            sys.exit(1)
    except ValueError:
        # Fallback verification in case response format varies
        if "success" not in response.text.lower():
            print(f"[!] UYAP response did not contain verification success: {response.text[:200]}")
            sys.exit(1)

    print("[+] Signature verified by UYAP.")


def confirm_login(session, verify_ssl=True):
    """Confirms the login state by sending a POST request with empty body to login.uyap."""
    print("[*] Step 5: Confirming login state (login.uyap)...")
    url = "https://avukat.uyap.gov.tr/login.uyap"
    
    session.headers.update({
        "Content-Type": "application/json",
        "Referer": "https://avukat.uyap.gov.tr/giris"
    })
    
    try:
        response = session.post(url, json={}, verify=verify_ssl, timeout=20)
    except Exception as e:
        print(f"[!] Error during login confirmation: {e}")
        sys.exit(1)

    if response.status_code != 200:
        print(f"[!] Login confirmation failed with status {response.status_code}.")
        sys.exit(1)

    print("[+] Login state confirmed.")


def verify_login_success(session, verify_ssl=True):
    """Queries a secure post-login endpoint (get_avukat_id.ajx) to confirm authorization."""
    print("[*] Step 6: Verifying login success & fetching Lawyer ID...")
    url = "https://avukat.uyap.gov.tr/get_avukat_id.ajx"
    
    session.headers.update({
        "Content-Type": "application/json",
        "Referer": "https://avukat.uyap.gov.tr/"
    })
    
    last_error_info = ""
    for attempt in range(3):
        try:
            response = session.post(url, json={}, verify=verify_ssl, timeout=20)
            if response.status_code == 200:
                avukat_id = response.text.strip()
                if avukat_id and avukat_id.isdigit():
                    print(f"[SUCCESS] LOGIN SUCCESSFUL! Verified Lawyer ID: {avukat_id}")
                    return avukat_id
                else:
                    last_error_info = f"Unexpected response: '{avukat_id}'"
            else:
                last_error_info = f"HTTP {response.status_code} - Body: {response.text[:200]}"
            print(f"[!] Step 6 attempt {attempt + 1} failed: {last_error_info}. Retrying...")
            time.sleep(1)
        except Exception as e:
            last_error_info = str(e)
            print(f"[!] Step 6 attempt {attempt + 1} error: {e}. Retrying...")
            time.sleep(1)

    # If all attempts fail, raise RuntimeError instead of sys.exit(1)
    raise RuntimeError(f"Failed to query lawyer ID endpoint: {last_error_info}")


def is_session_alive(session, verify_ssl=True):
    """Lightweight liveness probe. Returns True if the session can still reach the
    authenticated endpoint. Unlike verify_login_success() this never exits the process,
    so the keep-alive loop can decide for itself whether a re-login is required.
    """
    url = "https://avukat.uyap.gov.tr/get_avukat_id.ajx"
    try:
        response = session.post(
            url,
            json={},
            verify=verify_ssl,
            timeout=15,
            headers={
                "Content-Type": "application/json",
                "Referer": "https://avukat.uyap.gov.tr/",
            },
        )
    except Exception:
        return False

    if response.status_code != 200:
        return False

    avukat_id = response.text.strip()
    return bool(avukat_id) and avukat_id.isdigit()


def perform_login(args, verify_ssl, cert_id=None):
    """Runs the full e-imza login flow end-to-end and returns (session, cert_id_used).

    When cert_id is supplied (e.g. an automatic re-login triggered by the keep-alive
    loop) the certificate selection is non-interactive, so the process can renew the
    session unattended as long as the smart card stays inserted.
    """
    session = initialize_session(verify_ssl=verify_ssl)
    certs = get_certificates()

    selected_cert = select_certificate(certs, auto_cert_id=cert_id or args.cert_id)
    used_cert_id = selected_cert.get("certificateId")

    signed_data, tx_id = sign_payload(used_cert_id, args.pin)
    submit_signature(session, signed_data, tx_id, verify_ssl=verify_ssl)
    confirm_login(session, verify_ssl=verify_ssl)
    verify_login_success(session, verify_ssl=verify_ssl)
    return session, used_cert_id


def run_keepalive(session, cert_id, args, verify_ssl):
    """Keeps the captured session warm in memory.

    Each interval it probes the session; if UYAP has dropped it, it re-signs with the
    same certificate (the card must remain inserted at the office). Çerezler diske
    yazılmaz — oturum yalnızca bu süreçte, bellekte tutulur.
    """
    interval = args.interval
    print(f"\n[*] Keep-alive mode active. Checking every {interval}s. Press Ctrl+C to stop.")
    while True:
        time.sleep(interval)
        try:
            if is_session_alive(session, verify_ssl):
                print("[+] Session still valid.")
            else:
                print("[!] Session dropped. Re-authenticating with e-imza...")
                session, cert_id = perform_login(args, verify_ssl, cert_id=cert_id)
                print("[+] Session renewed.")
        except SystemExit:
            # A login helper aborted (e.g. card not ready yet). Don't kill the daemon; retry.
            print("[!] Re-login failed (card may not be ready). Retrying in 15s...")
            time.sleep(15)
        except Exception as e:
            print(f"[!] Keep-alive cycle error: {e}. Retrying in 15s...")
            time.sleep(15)


def main():
    parser = argparse.ArgumentParser(description="Programmatically login to UYAP Portal via Local E-Imza client.")
    parser.add_argument("--pin", type=str, default=os.environ.get("UYAP_PIN"),
                        help="E-imza PIN (ya da UYAP_PIN ortam değişkeni). Kaynağa YAZILMAZ.")
    parser.add_argument("--cert-id", type=str, default=None, help="Specific certificate ID to bypass interactive choice")
    parser.add_argument("--no-verify", action="store_true", help="Bypass SSL certificate validation")
    parser.add_argument("--once", action="store_true", help="Log in a single time and exit (default: keep the session alive)")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between keep-alive checks (default: 60)")
    args = parser.parse_args()

    if not args.pin:
        print("[!] PIN verilmedi. --pin ile ya da UYAP_PIN ortam değişkeniyle sağlayın "
              "(güvenlik gereği kaynağa gömülmez).")
        sys.exit(2)

    # Güvenlik raporu #10: --no-verify (TLS doğrulamasını kapat) YALNIZCA
    # UYAP_ALLOW_INSECURE_TLS=1 açıkça ayarlıyken geçerli; aksi halde YOK SAYILIR ve doğrulama
    # AÇIK kalır (fail-safe). Her durumda belirgin MITM uyarısı basılır.
    verify_ssl = True
    if args.no_verify:
        _insecure_ok = (os.environ.get("UYAP_ALLOW_INSECURE_TLS", "") or "").strip().lower() in ("1", "true", "yes", "on")
        _bar = "!" * 72
        if _insecure_ok:
            print("\n" + _bar)
            print("[GUVENLIK][UYARI] UYAP TLS sertifika dogrulamasi DEVRE DISI (--no-verify).")
            print("  Baglanti MITM'e ACIK. Yalnizca guvenilir gelistirme aginda kullanin!")
            print(_bar + "\n")
            verify_ssl = False
        else:
            print("\n" + _bar)
            print("[GUVENLIK] --no-verify YOK SAYILDI: UYAP TLS dogrulamasi ACIK tutuldu.")
            print("  Gercekten gerekiyorsa (yalnizca gelistirme) UYAP_ALLOW_INSECURE_TLS=1 ayarlayin.")
            print(_bar + "\n")

    print("==================================================")
    print("      UYAP E-IMZA PROGRAMMATIC LOGIN SIMULATION   ")
    print("==================================================")

    # Full login flow; cert_id is captured so unattended re-logins stay non-interactive.
    session, cert_id = perform_login(args, verify_ssl)

    print("\n--- Active Session Cookies ---")
    for name, val in session.cookies.get_dict().items():
        print(f"  {name}: {val}")

    print("\n[SUCCESS] Login process completed successfully. The session is held in memory only.")

    if args.once:
        input()
    else:
        run_keepalive(session, cert_id, args, verify_ssl)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Execution interrupted by user.")
        sys.exit(1)
