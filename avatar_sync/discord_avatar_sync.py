"""
discord_avatar_sync.py

Discordのアバター画像を取得し、「会員番号_探究ネーム」という名前で
Googleドライブにアップロードし、SpreadsheetのURL欄を更新するスクリプト。

事前準備:
  pip install requests gspread google-auth google-api-python-client python-dotenv --break-system-packages

.env に以下を設定:
  DISCORD_BOT_TOKEN=...
  GOOGLE_SERVICE_ACCOUNT_FILE=service_account.json
  SPREADSHEET_ID=...
  SHEET_NAME=探究者DB
  DRIVE_FOLDER_ID=...
"""

import os
import time
import requests
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"]
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
SHEET_NAME = os.environ.get("SHEET_NAME", "探究者DB")
DRIVE_FOLDER_ID = os.environ["DRIVE_FOLDER_ID"]

# Sheetの列名（1行目のヘッダーと一致させる）
COL_MEMBER_NO = "会員番号"
COL_TANQ = "探究ネーム"
COL_DISCORD_ID = "DiscordユーザーID"
COL_PHOTO_URL = "写真URL"

SCOPES = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]


def get_discord_avatar_url(user_id: str) -> str | None:
    """DiscordユーザーIDからアバター画像のURLを取得する"""
    url = f"https://discord.com/api/v10/users/{user_id}"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    res = requests.get(url, headers=headers)

    if res.status_code != 200:
        print(f"  ! Discord APIエラー (user_id={user_id}): {res.status_code} {res.text}")
        return None

    data = res.json()
    avatar_hash = data.get("avatar")
    if not avatar_hash:
        # アバター未設定の場合はデフォルトアイコンなので対象外とする
        return None

    ext = "gif" if avatar_hash.startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}?size=256"


def download_image(url: str) -> bytes:
    res = requests.get(url)
    res.raise_for_status()
    return res.content


def find_existing_file(drive_service, filename: str) -> str | None:
    """同名ファイルがフォルダ内に既にあるか検索し、あればファイルIDを返す"""
    query = f"name = '{filename}' and '{DRIVE_FOLDER_ID}' in parents and trashed = false"
    res = drive_service.files().list(q=query, fields="files(id)").execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def upload_or_update_image(drive_service, filename: str, image_bytes: bytes, mime_type: str) -> str:
    """画像をアップロード（既存なら上書き）し、表示用URLを返す"""
    media = MediaInMemoryUpload(image_bytes, mimetype=mime_type, resumable=False)
    existing_id = find_existing_file(drive_service, filename)

    if existing_id:
        drive_service.files().update(fileId=existing_id, media_body=media).execute()
        file_id = existing_id
    else:
        file_metadata = {"name": filename, "parents": [DRIVE_FOLDER_ID]}
        created = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
        file_id = created["id"]
        # 個別ファイルにも「リンクを知っている全員が閲覧者」権限を付与
        drive_service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()

    return f"https://lh3.googleusercontent.com/d/{file_id}"


def main():
    creds = Credentials.from_service_account_file(GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    drive_service = build("drive", "v3", credentials=creds)

    sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)

    # get_all_records() はヘッダー行に空欄や重複があるとエラーになるため、
    # 生データを取得して必要な列だけを自分で探す
    all_values = sheet.get_all_values()
    headers = all_values[0]
    data_rows = all_values[1:]

    col_member_no = headers.index(COL_MEMBER_NO)
    col_tanq = headers.index(COL_TANQ)
    col_discord_id = headers.index(COL_DISCORD_ID)
    col_photo_idx = headers.index(COL_PHOTO_URL) + 1  # gspreadのupdate_cellは1始まり

    for i, row in enumerate(data_rows, start=2):  # 2行目から（1行目はヘッダー）
        member_no = (row[col_member_no] if col_member_no < len(row) else "").strip()
        tanq = (row[col_tanq] if col_tanq < len(row) else "").strip()
        discord_id = (row[col_discord_id] if col_discord_id < len(row) else "").strip()

        if not discord_id or not member_no or not tanq:
            continue

        print(f"[{i}] {member_no}_{tanq} (Discord ID: {discord_id})")

        avatar_url = get_discord_avatar_url(discord_id)
        if not avatar_url:
            print("  - アバターなし。スキップ")
            continue

        image_bytes = download_image(avatar_url)
        mime_type = "image/gif" if avatar_url.endswith(".gif") else "image/png"
        ext = "gif" if mime_type == "image/gif" else "png"
        filename = f"{member_no}_{tanq}.{ext}"

        drive_url = upload_or_update_image(drive_service, filename, image_bytes, mime_type)
        sheet.update_cell(i, col_photo_idx, drive_url)
        print(f"  -> {filename} を更新しました")

        time.sleep(0.5)  # Discord APIのレート制限対策

    print("完了しました。")


if __name__ == "__main__":
    main()
