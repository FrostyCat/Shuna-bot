import json
import os

from dotenv import load_dotenv
from flask import Flask, abort, render_template

from db import Session
from models import Transcript

load_dotenv()

app = Flask(__name__)


@app.route("/transcript/<string:token>")
def transcript(token):
    session = Session()
    t = session.query(Transcript).filter_by(token=token).first()
    session.close()

    if not t:
        abort(404)

    messages = json.loads(t.messages)
    return render_template("transcript.html", transcript=t, messages=messages)


@app.route("/")
def index():
    abort(404)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
