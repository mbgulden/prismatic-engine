#!/usr/bin/env python3
"""
Google Docs write helper — uses existing OAuth keys, adds write scopes.
Run once to authorize, then use create_doc() and update_doc() programmatically.

Auth flow:
  1. python3 gdocs_helper.py auth
  2. Visit the printed URL, authorize, copy the redirect URL from your browser address bar
  3. Paste the full URL (contains ?code=... parameter)
  4. Token saved to ~/.config/mcp-gdrive/.gdocs-write-token.pickle

Prereqs: pip install --break-system-packages google-api-python-client google-auth-oauthlib
"""
import json, os, pickle, sys, urllib.parse
from pathlib import Path
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

CREDS_DIR = Path("$PRISMATIC_HOME/.config/mcp-gdrive")
KEYS_PATH = CREDS_DIR / "gcp-oauth.keys.json"
WRITE_TOKEN_PATH = CREDS_DIR / ".gdocs-write-token.pickle"

# Minimal write scopes — only files we create, not the whole Drive
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
]

def get_creds():
    """Get or refresh credentials. Opens browser for first-time auth."""
    creds = None
    if WRITE_TOKEN_PATH.exists():
        with open(WRITE_TOKEN_PATH, "rb") as f:
            creds = pickle.load(f)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not KEYS_PATH.exists():
                raise FileNotFoundError(f"OAuth keys not found at {KEYS_PATH}")
            flow = InstalledAppFlow.from_client_secrets_file(str(KEYS_PATH), SCOPES)
            
            # Console auth: user visits URL, copies redirect URL with code
            auth_url, _ = flow.authorization_url(
                access_type="offline", prompt="consent"
            )
            print("\n" + "=" * 60)
            print("Visit this URL to authorize Google Docs write access:")
            print(auth_url)
            print("=" * 60)
            print("\nAfter authorizing, your browser will redirect to localhost.")
            print("Copy the ENTIRE redirect URL from your address bar and paste it below.")
            redirect_url = input("\nPaste redirect URL: ").strip()
            
            # Extract code from redirect URL
            parsed = urllib.parse.urlparse(redirect_url)
            params = urllib.parse.parse_qs(parsed.query)
            code = params.get("code", [None])[0]
            if not code:
                raise ValueError("No 'code' parameter found in URL. Make sure you copied the full redirect URL.")
            
            flow.fetch_token(code=code)
            creds = flow.credentials
        
        with open(WRITE_TOKEN_PATH, "wb") as f:
            pickle.dump(creds, f)
    
    return creds

def create_doc(title: str, content: str = "") -> str:
    """Create a new Google Doc and optionally insert content. Returns doc ID."""
    creds = get_creds()
    docs_service = build("docs", "v1", credentials=creds)
    
    doc = docs_service.documents().create(body={"title": title}).execute()
    doc_id = doc.get("documentId")
    
    if content:
        requests = [{
            "insertText": {
                "location": {"index": 1},
                "text": content
            }
        }]
        docs_service.documents().batchUpdate(
            documentId=doc_id, body={"requests": requests}
        ).execute()
    
    print(f"✅ Created: {title}")
    print(f"   https://docs.google.com/document/d/{doc_id}/edit")
    return doc_id

def update_doc(doc_id: str, content: str, clear_first: bool = True):
    """Replace or append content to an existing doc."""
    creds = get_creds()
    docs_service = build("docs", "v1", credentials=creds)
    
    doc = docs_service.documents().get(documentId=doc_id).execute()
    end_index = doc.get("body", {}).get("content", [{}])[-1].get("endIndex", 1)
    
    requests = []
    
    if clear_first and end_index > 1:
        requests.append({
            "deleteContentRange": {
                "range": {"startIndex": 1, "endIndex": end_index - 1}
            }
        })
    
    requests.append({
        "insertText": {
            "location": {"index": 1},
            "text": content
        }
    })
    
    docs_service.documents().batchUpdate(
        documentId=doc_id, body={"requests": requests}
    ).execute()
    
    print(f"✅ Updated: https://docs.google.com/document/d/{doc_id}/edit")

def list_our_docs():
    """List docs created by this app."""
    creds = get_creds()
    drive_service = build("drive", "v3", credentials=creds)
    
    results = drive_service.files().list(
        pageSize=20,
        fields="files(id, name, createdTime)",
        orderBy="createdTime desc"
    ).execute()
    
    return results.get("files", [])

def create_folder(name: str, parent_id: str = None) -> str:
    """Create a folder in Drive. Returns folder ID."""
    creds = get_creds()
    drive_service = build("drive", "v3", credentials=creds)
    
    folder_metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder"
    }
    if parent_id:
        folder_metadata["parents"] = [parent_id]
    
    folder = drive_service.files().create(body=folder_metadata, fields="id").execute()
    return folder.get("id")

def move_to_folder(file_id: str, folder_id: str):
    """Move a file into a folder."""
    creds = get_creds()
    drive_service = build("drive", "v3", credentials=creds)
    
    file = drive_service.files().get(fileId=file_id, fields="parents").execute()
    previous_parents = ",".join(file.get("parents", []))
    
    drive_service.files().update(
        fileId=file_id,
        addParents=folder_id,
        removeParents=previous_parents,
        fields="id, parents"
    ).execute()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python gdocs_helper.py auth        — authorize write access")
        print("       python gdocs_helper.py create TITLE — create a doc")
        print("       python gdocs_helper.py list         — list our docs")
        sys.exit(0)
    
    cmd = sys.argv[1]
    
    if cmd == "auth":
        creds = get_creds()
        print("✅ Write access authorized!")
        print(f"   Token saved to {WRITE_TOKEN_PATH}")
    
    elif cmd == "create":
        title = sys.argv[2] if len(sys.argv) > 2 else "Untitled"
        create_doc(title)
    
    elif cmd == "list":
        docs = list_our_docs()
        if docs:
            for d in docs:
                print(f"  {d['name']} — {d['id']} ({d['createdTime'][:10]})")
        else:
            print("  No docs yet.")
    
    else:
        print(f"Unknown command: {cmd}")
