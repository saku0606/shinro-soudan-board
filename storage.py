"""データ保存の仕組み。

環境変数 AZURE_STORAGE_CONNECTION_STRING があれば Azure の
Table Storage（投稿・回答）と Blob Storage（画像）を使う。
なければメモリに保存する（ローカル確認用。再起動で消える）。
"""
import os
import time
import uuid
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
CONN = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
TABLE_NAME = "posts"
CONTAINER = "images"


def _now():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def _new_key():
    # 新しい順に並ぶよう「大きい数 - 時刻」をキーにする
    return f"{10**13 - int(time.time() * 1000):013d}-{uuid.uuid4().hex[:6]}"


class MemoryStore:
    kind = "memory"

    def __init__(self):
        self.rows = {}   # (PartitionKey, RowKey) -> dict
        self.blobs = {}  # name -> (bytes, content_type)

    def _query(self, pk):
        rows = [r for (p, _), r in self.rows.items() if p == pk]
        return sorted(rows, key=lambda r: r["RowKey"])

    def _upsert(self, row):
        self.rows[(row["PartitionKey"], row["RowKey"])] = dict(row)

    def _get(self, pk, rk):
        r = self.rows.get((pk, rk))
        return dict(r) if r else None

    def save_image(self, name, data, content_type):
        self.blobs[name] = (data, content_type)

    def load_image(self, name):
        return self.blobs.get(name)


class AzureStore:
    kind = "azure"

    def __init__(self, conn):
        from azure.data.tables import TableServiceClient
        from azure.storage.blob import BlobServiceClient

        self.table = TableServiceClient.from_connection_string(conn).create_table_if_not_exists(TABLE_NAME)
        blob_service = BlobServiceClient.from_connection_string(conn)
        self.container = blob_service.get_container_client(CONTAINER)
        if not self.container.exists():
            self.container.create_container()

    def _query(self, pk):
        rows = self.table.query_entities("PartitionKey eq @pk", parameters={"pk": pk})
        return sorted((dict(r) for r in rows), key=lambda r: r["RowKey"])

    def _upsert(self, row):
        self.table.upsert_entity(row)

    def _get(self, pk, rk):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            return dict(self.table.get_entity(pk, rk))
        except ResourceNotFoundError:
            return None

    def save_image(self, name, data, content_type):
        from azure.storage.blob import ContentSettings
        self.container.upload_blob(name, data, overwrite=True,
                                   content_settings=ContentSettings(content_type=content_type))

    def load_image(self, name):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            blob = self.container.get_blob_client(name)
            props = blob.get_blob_properties()
            return blob.download_blob().readall(), props.content_settings.content_type
        except ResourceNotFoundError:
            return None


class Board:
    """掲示板の操作（質問・回答・わかった！）"""

    def __init__(self):
        self.store = AzureStore(CONN) if CONN else MemoryStore()

    @property
    def kind(self):
        return self.store.kind

    def list_posts(self, subject=None):
        posts = self.store._query("post")
        if subject:
            posts = [p for p in posts if p.get("subject") == subject]
        return posts

    def get_post(self, post_id):
        return self.store._get("post", post_id)

    def add_post(self, nickname, grade, subject, title, body, image=None):
        post_id = _new_key()
        row = {
            "PartitionKey": "post", "RowKey": post_id,
            "nickname": nickname, "grade": grade, "subject": subject,
            "title": title, "body": body, "created": _now(),
            "replies": 0, "understood": 0, "image": "",
        }
        if image:
            data, content_type, ext = image
            name = f"{post_id}{ext}"
            self.store.save_image(name, data, content_type)
            row["image"] = name
        self.store._upsert(row)
        return post_id

    def list_replies(self, post_id):
        # 回答は古い順に表示したいので逆順
        return list(reversed(self.store._query(f"reply-{post_id}")))

    def add_reply(self, post_id, nickname, body):
        post = self.get_post(post_id)
        if not post:
            return False
        self.store._upsert({
            "PartitionKey": f"reply-{post_id}", "RowKey": _new_key(),
            "nickname": nickname, "body": body, "created": _now(),
        })
        post["replies"] = int(post.get("replies", 0)) + 1
        self.store._upsert(post)
        return True

    def understood(self, post_id):
        post = self.get_post(post_id)
        if post:
            post["understood"] = int(post.get("understood", 0)) + 1
            self.store._upsert(post)

    def load_image(self, name):
        return self.store.load_image(name)
