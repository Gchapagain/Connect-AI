import sqlite3
import os
import json
import secrets
import string
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(__file__), 'connectai.db')
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')

# ---------------------------------------------------------------------------
# 20 connection-building survey questions (16Personalities style, 1–7 scale)
# 1 = Strongly Agree  …  4 = Neutral  …  7 = Strongly Disagree
# ---------------------------------------------------------------------------
SURVEY_QUESTIONS = [
    "You feel energized after spending time with a large group of people.",
    "When someone shares a problem with you, your first instinct is to offer solutions.",
    "You find it easy to start conversations with strangers.",
    "You often notice when someone is feeling off, even if they haven't said anything.",
    "You prefer working through a problem alone before asking others for help.",
    "You genuinely enjoy being the person others turn to when things get hard.",
    "You find deep one-on-one conversations more satisfying than group hangouts.",
    "You tend to stay calm and grounded when people around you are stressed.",
    "You enjoy teaching or explaining things to others.",
    "You feel a strong sense of responsibility toward the people you care about.",
    "You find it easy to adjust how you communicate depending on who you're talking to.",
    "You would rather listen for a long time before sharing your own opinion.",
    "You enjoy mentoring, tutoring, or coaching others.",
    "You find it natural to check in on friends and classmates without being asked.",
    "When you're going through something hard, you prefer to talk it out with someone.",
    "You make friends easily in new environments or classes.",
    "You feel fulfilled when someone tells you that your support made a real difference.",
    "You tend to think carefully about how your words might affect someone before speaking.",
    "You're comfortable with silence during conversations — not every moment needs to be filled.",
    "You believe that the best help you can give someone is to really listen without judgment.",
]

SCORE_LABELS = {
    1: "Strongly Agree", 2: "Agree", 3: "Slightly Agree", 4: "Neutral",
    5: "Slightly Disagree", 6: "Disagree", 7: "Strongly Disagree",
}

# ---------------------------------------------------------------------------
# Seed data — 4 diverse TCU students with new 1-7 scores
# ---------------------------------------------------------------------------
SEED_USERS = [
    {
        "name": "Alex Martinez", "first_name": "Alex", "last_name": "Martinez",
        "email": "a.martinez@tcu.edu", "password": "Password1!",
        "phone": "817-555-0101", "dob": "2002-03-15",
        "graduation": "Spring 2025", "gender": "Male",
        "scores": [6, 2, 5, 5, 2, 4, 2, 2, 3, 3, 4, 3, 3, 5, 6, 5, 3, 2, 2, 5],
    },
    {
        "name": "Jordan Williams", "first_name": "Jordan", "last_name": "Williams",
        "email": "j.williams@tcu.edu", "password": "Password1!",
        "phone": "817-555-0202", "dob": "2003-07-22",
        "graduation": "Fall 2025", "gender": "Female",
        "scores": [1, 2, 1, 3, 6, 1, 6, 3, 2, 3, 2, 6, 2, 2, 1, 1, 2, 4, 6, 5],
    },
    {
        "name": "Sam Chen", "first_name": "Sam", "last_name": "Chen",
        "email": "s.chen@tcu.edu", "password": "Password1!",
        "phone": "817-555-0303", "dob": "2003-11-08",
        "graduation": "Spring 2026", "gender": "Non-binary",
        "scores": [3, 6, 3, 1, 5, 2, 2, 2, 3, 1, 2, 1, 3, 1, 3, 3, 1, 1, 2, 1],
    },
    {
        "name": "Taylor Johnson", "first_name": "Taylor", "last_name": "Johnson",
        "email": "t.johnson@tcu.edu", "password": "Password1!",
        "phone": "817-555-0404", "dob": "2002-05-30",
        "graduation": "Fall 2026", "gender": "Female",
        "scores": [5, 5, 4, 2, 2, 4, 2, 3, 2, 3, 3, 2, 4, 3, 5, 4, 3, 2, 2, 2],
    },
]

SEED_EMAILS = {u["email"] for u in SEED_USERS}


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _add_column_if_missing(cursor, table, col, col_type):
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
    except sqlite3.OperationalError:
        pass  # column already exists


def init_db():
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    conn = get_db()
    cursor = conn.cursor()

    # ── Create all tables ─────────────────────────────────────────────────
    cursor.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            first_name    TEXT,
            last_name     TEXT,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            phone         TEXT,
            dob           TEXT,
            graduation    TEXT,
            gender        TEXT,
            profile_photo TEXT,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS survey_answers (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer   TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS matches (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            seeker_id          INTEGER,
            helper_id          INTEGER,
            requester_id       INTEGER,
            matched_user_id    INTEGER,
            stress_description TEXT NOT NULL DEFAULT '',
            help_offered       TEXT,
            claude_explanation TEXT,
            pre_match_answers  TEXT,
            status             TEXT DEFAULT 'pending',
            timestamp          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            connected          INTEGER DEFAULT 0,
            FOREIGN KEY (seeker_id) REFERENCES users(id),
            FOREIGN KEY (helper_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS messages (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id  INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            content   TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_read   INTEGER DEFAULT 0,
            FOREIGN KEY (match_id)  REFERENCES matches(id),
            FOREIGN KEY (sender_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS admin_logs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            action         TEXT NOT NULL,
            target_user_id INTEGER,
            note           TEXT,
            timestamp      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            type       TEXT NOT NULL,
            message    TEXT NOT NULL,
            is_read    INTEGER DEFAULT 0,
            match_id   INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id)  REFERENCES users(id),
            FOREIGN KEY (match_id) REFERENCES matches(id)
        );
    ''')
    conn.commit()

    # ── Safe migrations ───────────────────────────────────────────────────
    for col, ctype in [
        ("first_name", "TEXT"), ("last_name", "TEXT"),
        ("profile_photo", "TEXT"), ("is_banned", "INTEGER DEFAULT 0"),
        ("help_offered", "TEXT"),
    ]:
        _add_column_if_missing(cursor, "users", col, ctype)
    for col, ctype in [
        ("seeker_id", "INTEGER"), ("helper_id", "INTEGER"),
        ("help_offered", "TEXT"), ("pre_match_answers", "TEXT"),
        ("status", "TEXT DEFAULT 'pending'"),
        ("compatibility_score", "INTEGER"),
        ("match_reason", "TEXT"),
        ("conversation_starter", "TEXT"),
        ("estimated_help_type", "TEXT"),
        ("runner_up_id", "INTEGER"),
        ("runner_up_score", "INTEGER"),
        ("helper_response_deadline", "TIMESTAMP"),
        ("seeker_notified", "INTEGER DEFAULT 0"),
        ("helper_notified", "INTEGER DEFAULT 0"),
    ]:
        _add_column_if_missing(cursor, "matches", col, ctype)
    conn.commit()

    # ── Back-fill first_name / last_name ──────────────────────────────────
    cursor.execute("""
        UPDATE users SET
            first_name = CASE WHEN instr(name,' ')>0
                         THEN substr(name,1,instr(name,' ')-1) ELSE name END,
            last_name  = CASE WHEN instr(name,' ')>0
                         THEN substr(name,instr(name,' ')+1) ELSE '' END
        WHERE first_name IS NULL OR first_name = ''
    """)
    conn.commit()

    _seed_data(conn)
    conn.close()


def _needs_survey_reseed(conn):
    """True when seed users still carry old text-format answers."""
    old_q = "When working on a group project, I prefer to..."
    return conn.execute(
        "SELECT COUNT(*) FROM survey_answers WHERE question=?", (old_q,)
    ).fetchone()[0] > 0


def _seed_data(conn):
    cursor = conn.cursor()
    existing = {r[0] for r in cursor.execute("SELECT email FROM users").fetchall()}
    needs_reseed = _needs_survey_reseed(conn)

    for u in SEED_USERS:
        if u["email"] not in existing:
            pw_hash = generate_password_hash(u["password"])
            cursor.execute(
                "INSERT INTO users (name,first_name,last_name,email,password_hash,"
                "phone,dob,graduation,gender) VALUES (?,?,?,?,?,?,?,?,?)",
                (u["name"], u["first_name"], u["last_name"], u["email"], pw_hash,
                 u["phone"], u["dob"], u["graduation"], u["gender"]),
            )
            uid = cursor.lastrowid
        else:
            uid = cursor.execute(
                "SELECT id FROM users WHERE email=?", (u["email"],)
            ).fetchone()[0]
            cursor.execute(
                "UPDATE users SET first_name=?,last_name=? WHERE id=?",
                (u["first_name"], u["last_name"], uid),
            )

        existing_answers = cursor.execute(
            "SELECT COUNT(*) FROM survey_answers WHERE user_id=?", (uid,)
        ).fetchone()[0]

        if needs_reseed or existing_answers == 0:
            cursor.execute("DELETE FROM survey_answers WHERE user_id=?", (uid,))
            for q, score in zip(SURVEY_QUESTIONS, u["scores"]):
                cursor.execute(
                    "INSERT INTO survey_answers (user_id,question,answer) VALUES (?,?,?)",
                    (uid, q, str(score)),
                )

    conn.commit()


# ---------------------------------------------------------------------------
# User queries
# ---------------------------------------------------------------------------

def get_user_by_email(email):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    conn.close()
    return u


def get_user_by_id(user_id):
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return u


def create_user(first_name, last_name, email, password_hash, phone, dob,
                graduation, gender, profile_photo=None):
    name = f"{first_name} {last_name}".strip()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (name,first_name,last_name,email,password_hash,"
            "phone,dob,graduation,gender,profile_photo) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, first_name, last_name, email, password_hash,
             phone, dob, graduation, gender, profile_photo),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        conn.close()
        return user, None
    except sqlite3.IntegrityError:
        conn.close()
        return None, "An account with this email already exists."


# ---------------------------------------------------------------------------
# Survey queries
# ---------------------------------------------------------------------------

def has_completed_survey(user_id):
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM survey_answers WHERE user_id=?", (user_id,)
    ).fetchone()[0]
    conn.close()
    return count >= len(SURVEY_QUESTIONS)


def save_survey_answers(user_id, scores: list):
    conn = get_db()
    conn.execute("DELETE FROM survey_answers WHERE user_id=?", (user_id,))
    for q, score in zip(SURVEY_QUESTIONS, scores):
        conn.execute(
            "INSERT INTO survey_answers (user_id,question,answer) VALUES (?,?,?)",
            (user_id, q, str(score)),
        )
    conn.commit()
    conn.close()


def get_survey_answers(user_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT question,answer FROM survey_answers WHERE user_id=?", (user_id,)
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Match queries
# ---------------------------------------------------------------------------

def get_active_match_for_user(user_id):
    conn = get_db()
    m = conn.execute("""
        SELECT * FROM matches
        WHERE (seeker_id=? OR helper_id=?) AND status IN ('pending','accepted')
        ORDER BY timestamp DESC LIMIT 1
    """, (user_id, user_id)).fetchone()
    conn.close()
    return m


def get_available_users_with_surveys(exclude_user_id):
    conn = get_db()
    locked_rows = conn.execute("""
        SELECT seeker_id FROM matches WHERE status IN ('pending','accepted')
        UNION
        SELECT helper_id  FROM matches WHERE status IN ('pending','accepted')
    """).fetchall()
    locked = {r[0] for r in locked_rows if r[0] is not None}
    locked.add(exclude_user_id)

    result = []
    # Also exclude banned users
    for u in conn.execute(
        "SELECT * FROM users WHERE is_banned=0 OR is_banned IS NULL"
    ).fetchall():
        if u["id"] in locked:
            continue
        answers = conn.execute(
            "SELECT question,answer FROM survey_answers WHERE user_id=? ORDER BY id",
            (u["id"],),
        ).fetchall()
        if len(answers) >= len(SURVEY_QUESTIONS):
            result.append({"user": u, "answers": answers})
    conn.close()
    return result


def create_match(seeker_id, helper_id, stress_description, help_offered, explanation,
                 compatibility_score=None, match_reason=None, conversation_starter=None,
                 estimated_help_type=None, runner_up_id=None, runner_up_score=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO matches (seeker_id,helper_id,requester_id,matched_user_id,"
        "stress_description,help_offered,claude_explanation,status,"
        "compatibility_score,match_reason,conversation_starter,estimated_help_type,"
        "runner_up_id,runner_up_score,helper_response_deadline) "
        "VALUES (?,?,?,?,?,?,?,'pending',?,?,?,?,?,?,datetime('now','+48 hours'))",
        (seeker_id, helper_id, seeker_id, helper_id,
         stress_description, help_offered, explanation,
         compatibility_score, match_reason, conversation_starter, estimated_help_type,
         runner_up_id, runner_up_score),
    )
    conn.commit()
    match_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return match_id


def get_match_by_id(match_id):
    conn = get_db()
    m = conn.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    conn.close()
    return m


def update_match_status(match_id, status, pre_match_answers=None):
    conn = get_db()
    if pre_match_answers is not None:
        conn.execute(
            "UPDATE matches SET status=?,pre_match_answers=? WHERE id=?",
            (status, json.dumps(pre_match_answers), match_id),
        )
    else:
        conn.execute("UPDATE matches SET status=? WHERE id=?", (status, match_id))
    conn.commit()
    conn.close()


def get_seeker_matches(user_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT m.*, u.name AS helper_name, u.first_name AS helper_first,
               u.email AS helper_email, u.graduation AS helper_grad,
               u.profile_photo AS helper_photo
        FROM matches m JOIN users u ON u.id=m.helper_id
        WHERE m.seeker_id=? ORDER BY m.timestamp DESC
    """, (user_id,)).fetchall()
    conn.close()
    return rows


def get_helper_pending(user_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT m.*, u.name AS seeker_name, u.first_name AS seeker_first,
               u.email AS seeker_email, u.graduation AS seeker_grad,
               u.profile_photo AS seeker_photo
        FROM matches m JOIN users u ON u.id=m.seeker_id
        WHERE m.helper_id=? AND m.status='pending' ORDER BY m.timestamp DESC
    """, (user_id,)).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Message queries
# ---------------------------------------------------------------------------

def send_message(match_id, sender_id, content):
    conn = get_db()
    conn.execute(
        "INSERT INTO messages (match_id,sender_id,content) VALUES (?,?,?)",
        (match_id, sender_id, content),
    )
    conn.commit()
    conn.close()


def get_messages_for_match(match_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT msg.*, u.name AS sender_name, u.first_name AS sender_first,
               u.profile_photo AS sender_photo
        FROM messages msg JOIN users u ON u.id=msg.sender_id
        WHERE msg.match_id=? ORDER BY msg.timestamp ASC
    """, (match_id,)).fetchall()
    conn.close()
    return rows


def get_messages_after(match_id, after_id):
    """Return messages in match_id with id > after_id, newest first."""
    conn = get_db()
    rows = conn.execute("""
        SELECT msg.*, u.name AS sender_name, u.first_name AS sender_first,
               u.profile_photo AS sender_photo
        FROM messages msg JOIN users u ON u.id=msg.sender_id
        WHERE msg.match_id=? AND msg.id>? ORDER BY msg.timestamp ASC
    """, (match_id, after_id)).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Admin queries
# ---------------------------------------------------------------------------

def get_all_users():
    conn = get_db()
    rows = conn.execute("""
        SELECT u.*,
               (SELECT COUNT(*) FROM survey_answers sa WHERE sa.user_id=u.id) AS answer_count
        FROM users u ORDER BY u.created_at DESC
    """).fetchall()
    conn.close()
    return rows


def ban_user(user_id):
    conn = get_db()
    conn.execute("UPDATE users SET is_banned=1 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()


def unban_user(user_id):
    conn = get_db()
    conn.execute("UPDATE users SET is_banned=0 WHERE id=?", (user_id,))
    conn.commit()
    conn.close()


def delete_user(user_id):
    conn = get_db()
    # Remove messages sent by user, survey answers, matches, then user
    conn.execute("DELETE FROM messages WHERE sender_id=?", (user_id,))
    conn.execute("DELETE FROM survey_answers WHERE user_id=?", (user_id,))
    conn.execute(
        "DELETE FROM messages WHERE match_id IN "
        "(SELECT id FROM matches WHERE seeker_id=? OR helper_id=?)",
        (user_id, user_id),
    )
    conn.execute("DELETE FROM matches WHERE seeker_id=? OR helper_id=?", (user_id, user_id))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    conn.close()


def get_user_full_profile(user_id):
    conn = get_db()
    user    = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    answers = conn.execute(
        "SELECT question, answer FROM survey_answers WHERE user_id=? ORDER BY id",
        (user_id,),
    ).fetchall()
    conn.close()
    return user, answers


def get_all_matches_admin():
    conn = get_db()
    rows = conn.execute("""
        SELECT m.*,
               s.name AS seeker_name, s.first_name AS seeker_first,
               h.name AS helper_name, h.first_name AS helper_first,
               (SELECT COUNT(*) FROM messages msg WHERE msg.match_id=m.id) AS msg_count
        FROM matches m
        LEFT JOIN users s ON s.id=m.seeker_id
        LEFT JOIN users h ON h.id=m.helper_id
        ORDER BY m.timestamp DESC
    """).fetchall()
    conn.close()
    return rows


def cancel_match_admin(match_id):
    conn = get_db()
    conn.execute("UPDATE matches SET status='cancelled' WHERE id=?", (match_id,))
    conn.commit()
    conn.close()


def delete_match_admin(match_id):
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE match_id=?", (match_id,))
    conn.execute("DELETE FROM matches WHERE id=?", (match_id,))
    conn.commit()
    conn.close()


def get_all_conversations():
    conn = get_db()
    rows = conn.execute("""
        SELECT m.*,
               s.name AS seeker_name, s.first_name AS seeker_first,
               h.name AS helper_name, h.first_name AS helper_first,
               (SELECT COUNT(*) FROM messages msg WHERE msg.match_id=m.id) AS msg_count,
               (SELECT content FROM messages msg WHERE msg.match_id=m.id
                ORDER BY msg.timestamp DESC LIMIT 1) AS last_msg
        FROM matches m
        LEFT JOIN users s ON s.id=m.seeker_id
        LEFT JOIN users h ON h.id=m.helper_id
        WHERE m.status='accepted'
        ORDER BY m.timestamp DESC
    """).fetchall()
    conn.close()
    return rows


def get_messages_thread_admin(match_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT msg.*, u.name AS sender_name, u.first_name AS sender_first
        FROM messages msg JOIN users u ON u.id=msg.sender_id
        WHERE msg.match_id=? ORDER BY msg.timestamp ASC
    """, (match_id,)).fetchall()
    conn.close()
    return rows


def delete_message_admin(message_id):
    conn = get_db()
    conn.execute("DELETE FROM messages WHERE id=?", (message_id,))
    conn.commit()
    conn.close()


def reset_user_password(user_id):
    alphabet = string.ascii_letters + string.digits + "!@#$"
    temp_pw  = ''.join(secrets.choice(alphabet) for _ in range(12))
    conn = get_db()
    conn.execute(
        "UPDATE users SET password_hash=? WHERE id=?",
        (generate_password_hash(temp_pw), user_id),
    )
    conn.commit()
    conn.close()
    return temp_pw


def get_admin_stats():
    conn = get_db()
    total_users    = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_surveys  = conn.execute(
        "SELECT COUNT(DISTINCT user_id) FROM survey_answers"
    ).fetchone()[0]
    total_matches  = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    new_this_week  = conn.execute(
        "SELECT COUNT(*) FROM users WHERE created_at >= datetime('now','-7 days')"
    ).fetchone()[0]
    banned_users   = conn.execute(
        "SELECT COUNT(*) FROM users WHERE is_banned=1"
    ).fetchone()[0]
    conn.close()
    return {
        "total_users":    total_users,
        "total_surveys":  total_surveys,
        "total_matches":  total_matches,
        "total_messages": total_messages,
        "new_this_week":  new_this_week,
        "banned_users":   banned_users,
    }


def log_admin_action(action, target_user_id=None, note=""):
    conn = get_db()
    conn.execute(
        "INSERT INTO admin_logs (action, target_user_id, note) VALUES (?,?,?)",
        (action, target_user_id, note),
    )
    conn.commit()
    conn.close()


def get_admin_logs(limit=100):
    conn = get_db()
    rows = conn.execute("""
        SELECT al.*, u.name AS target_name
        FROM admin_logs al
        LEFT JOIN users u ON u.id=al.target_user_id
        ORDER BY al.timestamp DESC LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# User help_offered cache
# ---------------------------------------------------------------------------

def update_user_help_offered(user_id, help_offered):
    """Cache a user's latest help_offered text on their user row."""
    conn = get_db()
    conn.execute("UPDATE users SET help_offered=? WHERE id=?", (help_offered, user_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def create_notification(user_id, notif_type, message, match_id=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO notifications (user_id,type,message,match_id) VALUES (?,?,?,?)",
        (user_id, notif_type, message, match_id),
    )
    conn.commit()
    conn.close()


def get_notifications(user_id, limit=10):
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM notifications
        WHERE user_id=? ORDER BY created_at DESC LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()
    return rows


def get_unread_notification_count(user_id):
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0",
        (user_id,),
    ).fetchone()[0]
    conn.close()
    return count


def mark_notification_read(notification_id, user_id):
    conn = get_db()
    conn.execute(
        "UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?",
        (notification_id, user_id),
    )
    conn.commit()
    conn.close()


def mark_all_notifications_read(user_id):
    conn = get_db()
    conn.execute(
        "UPDATE notifications SET is_read=1 WHERE user_id=?", (user_id,)
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Expired-match handling
# ---------------------------------------------------------------------------

def get_expired_pending_matches():
    """Return pending matches whose 48-hour response deadline has passed."""
    conn = get_db()
    rows = conn.execute("""
        SELECT * FROM matches
        WHERE status='pending'
          AND helper_response_deadline IS NOT NULL
          AND helper_response_deadline < datetime('now')
    """).fetchall()
    conn.close()
    return rows


def expire_match(match_id):
    conn = get_db()
    conn.execute("UPDATE matches SET status='expired' WHERE id=?", (match_id,))
    conn.commit()
    conn.close()


def mark_messages_read(match_id, reader_id):
    conn = get_db()
    conn.execute(
        "UPDATE messages SET is_read=1 WHERE match_id=? AND sender_id!=?",
        (match_id, reader_id),
    )
    conn.commit()
    conn.close()


def get_unread_count(user_id):
    conn = get_db()
    count = conn.execute("""
        SELECT COUNT(*) FROM messages msg
        JOIN matches m ON m.id=msg.match_id
        WHERE (m.seeker_id=? OR m.helper_id=?)
          AND msg.sender_id!=? AND msg.is_read=0 AND m.status='accepted'
    """, (user_id, user_id, user_id)).fetchone()[0]
    conn.close()
    return count


def get_accepted_matches_for_user(user_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT m.*,
               s.name AS seeker_name,  s.first_name AS seeker_first,  s.profile_photo AS seeker_photo,
               h.name AS helper_name,  h.first_name AS helper_first,  h.profile_photo AS helper_photo,
               (SELECT COUNT(*) FROM messages msg
                WHERE msg.match_id=m.id AND msg.sender_id!=? AND msg.is_read=0) AS unread
        FROM matches m
        JOIN users s ON s.id=m.seeker_id
        JOIN users h ON h.id=m.helper_id
        WHERE (m.seeker_id=? OR m.helper_id=?) AND m.status='accepted'
        ORDER BY m.timestamp DESC
    """, (user_id, user_id, user_id)).fetchall()
    conn.close()
    return rows
