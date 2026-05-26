import os
import time
from functools import wraps
from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from database import (
    init_db, SURVEY_QUESTIONS, SCORE_LABELS, UPLOAD_FOLDER,
    get_user_by_email, get_user_by_id, create_user,
    has_completed_survey, save_survey_answers, get_survey_answers,
    get_active_match_for_user, get_available_users_with_surveys,
    create_match, get_match_by_id, update_match_status,
    get_seeker_matches, get_helper_pending,
    send_message, get_messages_for_match, get_messages_after,
    mark_messages_read, get_unread_count, get_accepted_matches_for_user,
    update_user_help_offered,
    # notifications
    create_notification, get_notifications,
    get_unread_notification_count, mark_notification_read, mark_all_notifications_read,
    # expiry
    get_expired_pending_matches, expire_match,
    # admin
    get_all_users, ban_user, unban_user, delete_user,
    get_user_full_profile, get_all_matches_admin,
    cancel_match_admin, delete_match_admin,
    get_all_conversations, get_messages_thread_admin, delete_message_admin,
    reset_user_password, get_admin_stats, log_admin_action, get_admin_logs,
)
from matchmaking import find_best_match, HELP_TYPE_ICONS
from notifications import (
    notify_seeker_matched, notify_helper_requested,
    notify_seeker_accepted, notify_seeker_declined, notify_match_expired,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "connectai-tcu-2024-xk9p!")

# ---------------------------------------------------------------------------
# Admin credentials (hardcoded — change before production)
# ---------------------------------------------------------------------------
ADMIN_USERNAME = "connectai_admin"
ADMIN_PASSWORD = "TCU2026secure!"

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def save_profile_photo(file, email):
    if not file or not file.filename or not allowed_file(file.filename):
        return None
    ext = file.filename.rsplit(".", 1)[1].lower()
    filename = f"{secure_filename(email.replace('@', '_'))}_{int(time.time())}.{ext}"
    file.save(os.path.join(UPLOAD_FOLDER, filename))
    return filename


@app.context_processor
def inject_globals():
    unread_msg   = 0
    unread_notif = 0
    notifs       = []
    if "user_id" in session:
        uid = session["user_id"]
        try:
            unread_msg   = get_unread_count(uid)
            unread_notif = get_unread_notification_count(uid)
            notifs       = get_notifications(uid, limit=5)
        except Exception:
            pass
    return {
        "unread_count":      unread_msg,
        "unread_notif":      unread_notif,
        "recent_notifs":     notifs,
    }


# ---------------------------------------------------------------------------
# Background: expire stale pending matches (runs at most once per 5 minutes)
# ---------------------------------------------------------------------------
_last_expiry_check = 0.0


@app.before_request
def check_expired_matches():
    global _last_expiry_check
    if not session.get("user_id"):
        return
    now = time.time()
    if now - _last_expiry_check < 300:   # throttle to once every 5 minutes
        return
    _last_expiry_check = now
    try:
        for expired in get_expired_pending_matches():
            expire_match(expired["id"])
            notify_match_expired(expired["seeker_id"], expired["id"])
    except Exception as exc:
        print(f"[expiry check] {exc}")


# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("dashboard") if "user_id" in session else url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        first  = request.form.get("first_name", "").strip()
        last   = request.form.get("last_name",  "").strip()
        email  = request.form.get("email", "").strip().lower()
        pw     = request.form.get("password", "")
        phone  = request.form.get("phone", "").strip()
        dob    = request.form.get("dob", "").strip()
        grad   = request.form.get("graduation", "").strip()
        gender = request.form.get("gender", "").strip()

        if not email.endswith("@tcu.edu"):
            flash("Only @tcu.edu email addresses are allowed.", "danger")
            return render_template("signup.html", form=request.form)
        if len(pw) < 8:
            flash("Password must be at least 8 characters.", "danger")
            return render_template("signup.html", form=request.form)
        if not all([first, last, email, pw, grad, gender]):
            flash("Please fill in all required fields.", "danger")
            return render_template("signup.html", form=request.form)

        photo = save_profile_photo(request.files.get("profile_photo"), email)
        user, err = create_user(first, last, email, generate_password_hash(pw),
                                phone, dob, grad, gender, photo)
        if err:
            flash(err, "danger")
            return render_template("signup.html", form=request.form)

        session["user_id"] = user["id"]
        session["user_name"] = user["first_name"]
        flash(f"Welcome to ConnectAI, {first}! Let's learn about you.", "success")
        return redirect(url_for("survey"))

    return render_template("signup.html", form={})


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw    = request.form.get("password", "")
        user  = get_user_by_email(email)

        if user and check_password_hash(user["password_hash"], pw):
            if user["is_banned"]:
                flash("Your account has been suspended. Contact support for help.", "danger")
                return render_template("login.html")
            session["user_id"]   = user["id"]
            session["user_name"] = user["first_name"] or user["name"].split()[0]
            flash(f"Welcome back, {session['user_name']}!", "success")
            return redirect(url_for("survey") if not has_completed_survey(user["id"])
                            else url_for("dashboard"))
        flash("Invalid email or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You've been logged out.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Survey
# ---------------------------------------------------------------------------

@app.route("/survey", methods=["GET", "POST"])
@login_required
def survey():
    user_id = session["user_id"]

    if request.method == "POST":
        scores = []
        missing = []
        for i, _ in enumerate(SURVEY_QUESTIONS, start=1):
            val = request.form.get(f"q{i}", "").strip()
            if not val or not val.isdigit() or not (1 <= int(val) <= 7):
                missing.append(i)
            else:
                scores.append(int(val))

        if missing:
            flash("Please answer every question before continuing.", "warning")
            return render_template("survey.html", questions=SURVEY_QUESTIONS,
                                   prev=request.form)

        save_survey_answers(user_id, scores)
        flash("Profile saved! Now tell us what's on your mind.", "success")
        return redirect(url_for("stress"))

    if has_completed_survey(user_id):
        return redirect(url_for("stress"))

    return render_template("survey.html", questions=SURVEY_QUESTIONS, prev={})


# ---------------------------------------------------------------------------
# Stress / Find a Buddy
# ---------------------------------------------------------------------------

@app.route("/stress", methods=["GET", "POST"])
@login_required
def stress():
    user_id = session["user_id"]
    if not has_completed_survey(user_id):
        flash("Please complete your personality survey first.", "warning")
        return redirect(url_for("survey"))

    active       = get_active_match_for_user(user_id)
    match_result = None

    if request.method == "POST":
        stress_text = request.form.get("stress_text", "").strip()
        help_text   = request.form.get("help_text",   "").strip()

        if not stress_text and not help_text:
            flash("Please fill in at least one section.", "warning")
            return render_template("stress.html", match_result=None, active=active)

        if active:
            flash("You're already in an active match. Check your dashboard.", "info")
            return redirect(url_for("dashboard"))

        # Cache the user's help offering on their profile row
        if help_text:
            update_user_help_offered(user_id, help_text)

        candidates = get_available_users_with_surveys(exclude_user_id=user_id)

        if not candidates:
            flash("No available peers right now — check back soon!", "info")
            return render_template("stress.html", match_result=None, active=active)

        try:
            result = find_best_match(
                seeker_id=user_id,
                seeker_stress=stress_text or help_text,
                seeker_help_offered=help_text,
                candidates=candidates,
            )

            if not result:
                flash("Couldn't find a match right now — try again soon!", "info")
                return render_template("stress.html", match_result=None, active=active)

            helper = result["helper"]
            match_id = create_match(
                seeker_id=user_id,
                helper_id=helper["id"],
                stress_description=stress_text or help_text,
                help_offered=help_text,
                explanation=result["match_reason"],
                compatibility_score=result["compatibility_score"],
                match_reason=result["match_reason"],
                conversation_starter=result["conversation_starter"],
                estimated_help_type=result["estimated_help_type"],
                runner_up_id=result.get("runner_up_id"),
                runner_up_score=result.get("runner_up_score"),
            )

            # Fire notifications
            seeker = get_user_by_id(user_id)
            notify_seeker_matched(user_id, match_id,
                                  helper["first_name"] or helper["name"].split()[0])
            notify_helper_requested(helper["id"], match_id,
                                    seeker["first_name"] or seeker["name"].split()[0])

            match_result = {
                "match_id":             match_id,
                "name":                 helper["name"],
                "first":                helper["first_name"] or helper["name"].split()[0],
                "graduation":           helper["graduation"],
                "email":                helper["email"],
                "photo":                helper.get("profile_photo"),
                "explanation":          result["match_reason"],
                "compatibility_score":  result["compatibility_score"],
                "conversation_starter": result["conversation_starter"],
                "estimated_help_type":  result["estimated_help_type"],
                "help_type_icon":       HELP_TYPE_ICONS.get(result["estimated_help_type"], "✨"),
                "method":               result.get("method", "gemini"),
            }

        except Exception as exc:
            flash(f"Matching hit a snag — please try again. ({exc})", "danger")

    return render_template("stress.html", match_result=match_result, active=active)


# ---------------------------------------------------------------------------
# Pre-match: helper responds to pending request
# ---------------------------------------------------------------------------

@app.route("/match/respond/<int:match_id>", methods=["GET", "POST"])
@login_required
def respond_match(match_id):
    user_id = session["user_id"]
    match   = get_match_by_id(match_id)

    if not match or match["helper_id"] != user_id:
        abort(403)
    if match["status"] != "pending":
        flash("This match has already been resolved.", "info")
        return redirect(url_for("dashboard"))

    seeker = get_user_by_id(match["seeker_id"])

    if request.method == "POST":
        decision     = request.form.get("decision", "")
        topic_ok     = request.form.get("topic_ok", "")
        available    = request.form.get("available", "")
        in_person    = request.form.get("in_person", "")
        confidence   = request.form.get("confidence", "")

        answers = {
            "topic_ok":   topic_ok,
            "available":  available,
            "in_person":  in_person,
            "confidence": confidence,
        }

        helper_user = get_user_by_id(user_id)
        helper_first = helper_user["first_name"] or helper_user["name"].split()[0]
        seeker_first = seeker["first_name"] or seeker["name"].split()[0]

        if decision == "accept":
            update_match_status(match_id, "accepted", answers)
            notify_seeker_accepted(match["seeker_id"], match_id, helper_first)
            flash(f"You accepted the match with {seeker_first}! "
                  f"Say hello in your messages. 💬", "success")
            return redirect(url_for("message_thread", match_id=match_id))
        else:
            update_match_status(match_id, "rejected", answers)
            notify_seeker_declined(match["seeker_id"], match_id)
            flash("No worries — we'll find someone else to help them.", "info")
            return redirect(url_for("dashboard"))

    return render_template("pre_match.html", match=match, seeker=seeker)


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@app.route("/messages")
@login_required
def messages_inbox():
    user_id = session["user_id"]
    threads = get_accepted_matches_for_user(user_id)
    return render_template("messages.html", threads=threads, thread=None,
                           msgs=[], match_id=None)


@app.route("/messages/<int:match_id>", methods=["GET", "POST"])
@login_required
def message_thread(match_id):
    user_id = session["user_id"]
    match   = get_match_by_id(match_id)

    if not match or match["status"] != "accepted":
        flash("This conversation is not available.", "warning")
        return redirect(url_for("messages_inbox"))
    if match["seeker_id"] != user_id and match["helper_id"] != user_id:
        abort(403)

    if request.method == "POST":
        content = request.form.get("content", "").strip()
        if content:
            send_message(match_id, user_id, content)
        return redirect(url_for("message_thread", match_id=match_id))

    mark_messages_read(match_id, user_id)
    msgs    = get_messages_for_match(match_id)
    threads = get_accepted_matches_for_user(user_id)
    other   = get_user_by_id(
        match["helper_id"] if match["seeker_id"] == user_id else match["seeker_id"]
    )
    return render_template("messages.html", threads=threads, thread=match,
                           msgs=msgs, match_id=match_id, other=other)


# ---------------------------------------------------------------------------
# Messages — JSON API (AJAX)
# ---------------------------------------------------------------------------

@app.route("/messages/<int:match_id>/send", methods=["POST"])
@login_required
def message_send(match_id):
    user_id = session["user_id"]
    match   = get_match_by_id(match_id)

    if not match or match["status"] != "accepted":
        return {"ok": False, "error": "not found"}, 404
    if match["seeker_id"] != user_id and match["helper_id"] != user_id:
        return {"ok": False, "error": "forbidden"}, 403

    content = (request.json or {}).get("content", "").strip()
    if not content:
        return {"ok": False, "error": "empty"}, 400

    send_message(match_id, user_id, content)
    # Return the message we just inserted so the client can append it instantly
    msgs = get_messages_for_match(match_id)
    last = msgs[-1] if msgs else None
    if last:
        user = get_user_by_id(user_id)
        return {
            "ok": True,
            "message": {
                "id":           last["id"],
                "sender_id":    last["sender_id"],
                "content":      last["content"],
                "timestamp":    last["timestamp"],
                "sender_first": user["first_name"] or user["name"].split()[0],
                "mine":         True,
            }
        }
    return {"ok": False, "error": "insert failed"}, 500


@app.route("/messages/<int:match_id>/fetch")
@login_required
def message_fetch(match_id):
    user_id  = session["user_id"]
    match    = get_match_by_id(match_id)

    if not match or match["status"] != "accepted":
        return {"ok": False, "error": "not found"}, 404
    if match["seeker_id"] != user_id and match["helper_id"] != user_id:
        return {"ok": False, "error": "forbidden"}, 403

    after_id = request.args.get("after", 0, type=int)
    rows     = get_messages_after(match_id, after_id)
    mark_messages_read(match_id, user_id)

    messages = [
        {
            "id":           r["id"],
            "sender_id":    r["sender_id"],
            "content":      r["content"],
            "timestamp":    r["timestamp"],
            "sender_first": r["sender_first"] or r["sender_name"].split()[0],
            "mine":         r["sender_id"] == user_id,
        }
        for r in rows
    ]
    return {"ok": True, "messages": messages}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/dashboard")
@login_required
def dashboard():
    user_id       = session["user_id"]
    user          = get_user_by_id(user_id)
    answers       = get_survey_answers(user_id)
    survey_done   = has_completed_survey(user_id)
    seeker_matches = get_seeker_matches(user_id)
    helper_pending = get_helper_pending(user_id)
    active_match   = get_active_match_for_user(user_id)
    inbox_threads  = get_accepted_matches_for_user(user_id)

    return render_template(
        "dashboard.html",
        user=user, answers=answers,
        survey_done=survey_done,
        seeker_matches=seeker_matches,
        helper_pending=helper_pending,
        active_match=active_match,
        inbox_threads=inbox_threads,
    )


# ---------------------------------------------------------------------------
# Notifications API
# ---------------------------------------------------------------------------

@app.route("/notifications/unread")
@login_required
def notifications_unread():
    count = get_unread_notification_count(session["user_id"])
    return {"count": count}


@app.route("/notifications/mark-read/<int:notif_id>", methods=["POST"])
@login_required
def notification_mark_read(notif_id):
    mark_notification_read(notif_id, session["user_id"])
    return {"ok": True}


@app.route("/notifications/mark-all-read", methods=["POST"])
@login_required
def notifications_mark_all_read():
    mark_all_notifications_read(session["user_id"])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Admin — Auth
# ---------------------------------------------------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("admin_logged_in"):
        return redirect(url_for("admin_dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            log_admin_action("admin_login", note="Admin logged in")
            return redirect(url_for("admin_dashboard"))
        error = "Invalid credentials."

    return render_template("admin_login.html", error=error)


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


# ---------------------------------------------------------------------------
# Admin — Dashboard
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin_dashboard():
    stats        = get_admin_stats()
    users        = get_all_users()
    matches      = get_all_matches_admin()
    convos       = get_all_conversations()
    logs         = get_admin_logs(50)
    temp_pw      = session.pop("temp_pw", None)
    temp_pw_user = session.pop("temp_pw_user", None)
    return render_template(
        "admin_dashboard.html",
        stats=stats, users=users, matches=matches,
        convos=convos, logs=logs,
        temp_pw=temp_pw, temp_pw_user=temp_pw_user,
    )


# ---------------------------------------------------------------------------
# Admin — User actions
# ---------------------------------------------------------------------------

@app.route("/admin/user/<int:user_id>")
@admin_required
def admin_view_user(user_id):
    user, answers = get_user_full_profile(user_id)
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for("admin_dashboard"))
    return render_template("admin_user.html", user=user, answers=answers,
                           score_labels=SCORE_LABELS)


@app.route("/admin/user/<int:user_id>/ban", methods=["POST"])
@admin_required
def admin_ban_user(user_id):
    user = get_user_by_id(user_id)
    if user:
        ban_user(user_id)
        log_admin_action("ban_user", target_user_id=user_id,
                         note=f"Banned {user['name']}")
        flash(f"Banned {user['name']}.", "warning")
    return redirect(url_for("admin_dashboard") + "#users")


@app.route("/admin/user/<int:user_id>/unban", methods=["POST"])
@admin_required
def admin_unban_user(user_id):
    user = get_user_by_id(user_id)
    if user:
        unban_user(user_id)
        log_admin_action("unban_user", target_user_id=user_id,
                         note=f"Unbanned {user['name']}")
        flash(f"Unbanned {user['name']}.", "success")
    return redirect(url_for("admin_dashboard") + "#users")


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    user = get_user_by_id(user_id)
    if user:
        name = user["name"]
        delete_user(user_id)
        log_admin_action("delete_user", note=f"Deleted user {name} (id={user_id})")
        flash(f"Deleted user {name} and all their data.", "danger")
    return redirect(url_for("admin_dashboard") + "#users")


@app.route("/admin/user/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def admin_reset_password(user_id):
    user = get_user_by_id(user_id)
    if user:
        temp_pw = reset_user_password(user_id)
        log_admin_action("reset_password", target_user_id=user_id,
                         note=f"Reset password for {user['name']}")
        session["temp_pw"]      = temp_pw
        session["temp_pw_user"] = user["name"]
        flash(f"Password reset for {user['name']}. Temporary password shown below.", "info")
    return redirect(url_for("admin_dashboard") + "#users")


# ---------------------------------------------------------------------------
# Admin — Match actions
# ---------------------------------------------------------------------------

@app.route("/admin/match/<int:match_id>/cancel", methods=["POST"])
@admin_required
def admin_cancel_match(match_id):
    cancel_match_admin(match_id)
    log_admin_action("cancel_match", note=f"Cancelled match id={match_id}")
    flash("Match cancelled.", "warning")
    return redirect(url_for("admin_dashboard") + "#matches")


@app.route("/admin/match/<int:match_id>/delete", methods=["POST"])
@admin_required
def admin_delete_match(match_id):
    delete_match_admin(match_id)
    log_admin_action("delete_match", note=f"Deleted match id={match_id}")
    flash("Match and all its messages deleted.", "danger")
    return redirect(url_for("admin_dashboard") + "#matches")


# ---------------------------------------------------------------------------
# Admin — Message moderation
# ---------------------------------------------------------------------------

@app.route("/admin/thread/<int:match_id>")
@admin_required
def admin_view_thread(match_id):
    match = get_match_by_id(match_id)
    if not match:
        flash("Match not found.", "danger")
        return redirect(url_for("admin_dashboard"))
    msgs = get_messages_thread_admin(match_id)
    seeker = get_user_by_id(match["seeker_id"])
    helper = get_user_by_id(match["helper_id"])
    return render_template("admin_thread.html", match=match, msgs=msgs,
                           seeker=seeker, helper=helper)


@app.route("/admin/message/<int:message_id>/delete", methods=["POST"])
@admin_required
def admin_delete_message(message_id):
    match_id = request.form.get("match_id", type=int)
    delete_message_admin(message_id)
    log_admin_action("delete_message", note=f"Deleted message id={message_id}")
    flash("Message deleted.", "danger")
    if match_id:
        return redirect(url_for("admin_view_thread", match_id=match_id))
    return redirect(url_for("admin_dashboard") + "#messages")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    print("\n  🎓 ConnectAI | TCU  →  http://127.0.0.1:5000\n")
    app.run(debug=True, host='0.0.0.0', port=8080)
