import os
from datetime import date, datetime

from flask import Flask, Response, abort, redirect, render_template, request, url_for

from storage import JST, Board

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024  # 画像は5MBまで（少し余裕）

board = Board()

SUBJECTS = ["文系", "理系", "医療・看護", "芸術・スポーツ", "就職・専門", "勉強法・メンタル"]  # 志望分野タグ
GRADES = ["1年", "2年", "3年"]
REPLY_GRADES = GRADES + ["卒業生"]  # 回答者は卒業生も選べる
RANK = {g: i for i, g in enumerate(REPLY_GRADES)}
SOLVED_AT = 3  # 「参考になった！」がこの数以上で「参考になる」バッジ
ALLOWED_IMAGES = {
    "image/jpeg": ".jpg", "image/png": ".png",
    "image/gif": ".gif", "image/webp": ".webp",
}
# 受験日は App Service の環境変数 TEST_DATE（例: 2027-01-16）で変更できる
TEST_NAME = os.environ.get("TEST_NAME", "共通テスト")
TEST_DATE = os.environ.get("TEST_DATE", "2027-01-16")


def days_left():
    try:
        target = date.fromisoformat(TEST_DATE)
    except ValueError:
        return None
    return (target - datetime.now(JST).date()).days


@app.context_processor
def common():
    return {
        "subjects": SUBJECTS, "grades": GRADES, "reply_grades": REPLY_GRADES, "solved_at": SOLVED_AT,
        "test_name": TEST_NAME, "days_left": days_left(), "store_kind": board.kind,
    }


def clean(name, limit):
    return (request.form.get(name) or "").strip()[:limit]


@app.route("/")
def index():
    subject = request.args.get("subject")
    if subject not in SUBJECTS:
        subject = None
    return render_template("index.html", posts=board.list_posts(subject), current=subject,
                           error=request.args.get("error"))


@app.route("/post", methods=["POST"])
def create_post():
    nickname = clean("nickname", 20) or "匿名さん"
    grade = request.form.get("grade") if request.form.get("grade") in GRADES else GRADES[0]
    subject = request.form.get("subject") if request.form.get("subject") in SUBJECTS else SUBJECTS[0]
    title = clean("title", 60)
    body = clean("body", 2000)
    if not title or not body:
        return redirect(url_for("index", error="タイトルと本文を入力してください"))

    image = None
    file = request.files.get("image")
    if file and file.filename:
        ext = ALLOWED_IMAGES.get(file.mimetype)
        if not ext:
            return redirect(url_for("index", error="画像は jpg / png / gif / webp だけ投稿できます"))
        data = file.read()
        if len(data) > 5 * 1024 * 1024:
            return redirect(url_for("index", error="画像は5MBまでです"))
        image = (data, file.mimetype, ext)

    post_id = board.add_post(nickname, grade, subject, title, body, image)
    return redirect(url_for("detail", post_id=post_id))


@app.route("/p/<post_id>")
def detail(post_id):
    post = board.get_post(post_id)
    if not post:
        abort(404)
    return render_template("post.html", post=post, replies=board.list_replies(post_id))


@app.route("/p/<post_id>/reply", methods=["POST"])
def reply(post_id):
    body = clean("body", 2000)
    post = board.get_post(post_id)
    if body and post:
        grade = request.form.get("grade") if request.form.get("grade") in REPLY_GRADES else GRADES[0]
        # 相談者より上の学年、または卒業生なら「先輩」
        senpai = RANK[grade] > RANK.get(post.get("grade"), 0)
        board.add_reply(post_id, clean("nickname", 20) or "匿名さん", grade, senpai, body)
    return redirect(url_for("detail", post_id=post_id) + "#replies")


@app.route("/p/<post_id>/understood", methods=["POST"])
def understood(post_id):
    board.understood(post_id)
    return redirect(url_for("detail", post_id=post_id))


@app.route("/img/<name>")
def image(name):
    """Blob Storage の画像を取り出して表示する"""
    found = board.load_image(name)
    if not found:
        if name == "logo.png":  # Blob にロゴがまだ無いときは同梱のロゴを使う
            return redirect(url_for("static", filename="logo.svg"))
        abort(404)
    data, content_type = found
    return Response(data, mimetype=content_type or "application/octet-stream",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.errorhandler(413)
def too_large(_):
    return redirect(url_for("index", error="画像は5MBまでです"))


if __name__ == "__main__":
    app.run(debug=True, port=8000)
