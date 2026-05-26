"""
ConnectAI — Sophisticated LLM-Powered Matchmaking Engine
Primary: Google Gemini (returns structured JSON)
Fallback: Local scoring algorithm
"""
import os
import json
import re

from google import genai
from google.genai import types as genai_types

# ---------------------------------------------------------------------------
# Personality dimension question indices (1-based, matching SURVEY_QUESTIONS)
# ---------------------------------------------------------------------------
EXTRAVERSION_Qs   = [1, 4, 7, 10, 13]   # sociability / energy with others
EMPATHY_Qs        = [2, 5, 8, 11, 14]   # emotional attunement / care
LEADERSHIP_Qs     = [3, 6, 9, 12, 15]   # initiative / teaching drive
PROBLEMSOLVING_Qs = [16, 17, 18, 19, 20] # thoughtfulness / communication

HELP_TYPE_ICONS = {
    "Academic":  "📚",
    "Social":    "🤝",
    "Emotional": "💙",
    "Career":    "💼",
    "Mixed":     "✨",
}

STOPWORDS = {
    "the","a","an","is","in","on","at","to","for","of","and","or","i",
    "my","me","am","are","was","be","have","has","with","this","that",
    "it","im","ive","just","really","very","so","do","not","no","get",
}


# ---------------------------------------------------------------------------
# Personality helpers
# ---------------------------------------------------------------------------

def _calc_dimension(answers, q_indices):
    """
    Average inverted score for a personality dimension.
    Survey answers: 1 = Strongly Agree (high trait), 7 = Strongly Disagree (low trait).
    We invert so that a higher returned value = stronger trait.
    answers: list of sqlite Row objects with an 'answer' field.
    """
    vals = []
    for idx in q_indices:
        if 1 <= idx <= len(answers):
            try:
                vals.append(int(answers[idx - 1]["answer"]))
            except (ValueError, IndexError, KeyError, TypeError):
                pass
    if not vals:
        return 4.0
    avg = sum(vals) / len(vals)
    return round(8.0 - avg, 2)  # invert: 1→7.0, 4→4.0, 7→1.0


def build_personality(answers):
    """Return dict of four personality dimension scores (all on 1-7 scale)."""
    return {
        "extraversion":    _calc_dimension(answers, EXTRAVERSION_Qs),
        "empathy":         _calc_dimension(answers, EMPATHY_Qs),
        "leadership":      _calc_dimension(answers, LEADERSHIP_Qs),
        "problem_solving": _calc_dimension(answers, PROBLEMSOLVING_Qs),
    }


# ---------------------------------------------------------------------------
# Local fallback scoring
# ---------------------------------------------------------------------------

def _keyword_overlap(text_a, text_b):
    """Return 0-30 pts based on keyword overlap between two strings."""
    if not text_a or not text_b:
        return 0
    wa = {w for w in text_a.lower().split() if w not in STOPWORDS and len(w) > 2}
    wb = {w for w in text_b.lower().split() if w not in STOPWORDS and len(w) > 2}
    if not wa or not wb:
        return 0
    overlap = wa & wb
    return int(len(overlap) / max(len(wa), len(wb)) * 30)


def _parse_year(grad_str):
    if not grad_str:
        return None
    m = re.search(r'\d{4}', str(grad_str))
    return int(m.group()) if m else None


def _local_score(seeker_info, helper_info, seeker_stress):
    score = 0
    # Keyword overlap: seeker stress vs helper's offered help (max 30)
    score += _keyword_overlap(seeker_stress, helper_info.get("help_offered") or "")
    # Graduation proximity (max 20)
    sy = _parse_year(seeker_info.get("graduation"))
    hy = _parse_year(helper_info.get("graduation"))
    if sy and hy:
        score += max(0, 20 - abs(sy - hy) * 7)
    else:
        score += 10
    # Helper empathy bonus (max 21 pts — empathetic helpers suit most needs)
    hp = helper_info.get("personality", {})
    sp = seeker_info.get("personality", {})
    if hp:
        score += int(hp.get("empathy", 4) * 3)
        # Complementary extraversion (max 10 pts)
        if sp:
            e_diff = abs(sp.get("extraversion", 4) - hp.get("extraversion", 4))
            score += min(10, int(e_diff * 2.5))
    return min(100, score)


# ---------------------------------------------------------------------------
# Gemini matching
# ---------------------------------------------------------------------------

def _try_gemini(seeker, helpers):
    """
    Call Gemini 1.5 Flash with a structured prompt.
    Returns a result dict or None on any failure.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return None
    try:
        client = genai.Client(api_key=api_key)
        sp = seeker["personality"]

        helpers_payload = [
            {
                "id":           h["id"],
                "name":         h["name"],
                "graduation":   h["graduation"],
                "help_offered": h["help_offered"] or "Available to help",
                "personality":  {k: round(v, 1) for k, v in h["personality"].items()},
            }
            for h in helpers
        ]

        prompt = f"""You are an expert peer-support matching counselor at TCU (Texas Christian University). Find the single best student to help another student.

SEEKER PROFILE:
Name: {seeker["name"]}
Graduation: {seeker["graduation"]}
What they are struggling with: {seeker["stress"]}
What they can offer in return: {seeker.get("help_offered") or "Not specified"}
Personality dimensions (1 = low trait, 7 = high trait):
  Extraversion:    {sp["extraversion"]}
  Empathy:         {sp["empathy"]}
  Leadership:      {sp["leadership"]}
  Problem-solving: {sp["problem_solving"]}

AVAILABLE HELPERS (JSON array):
{json.dumps(helpers_payload, indent=2)}

MATCHING CRITERIA (priority order):
1. Helper must have experience or skills relevant to the seeker's specific problem
2. Complementary personalities work best (e.g. a highly extroverted seeker needs a calm, empathetic helper)
3. Closer graduation years mean more shared context and experience
4. Helper's offered help should address what the seeker actually needs
5. Avoid matching students with nearly identical personality profiles — contrast drives growth

Return ONLY valid JSON with no markdown or extra text:
{{
  "best_match_id": <integer user_id from helpers list>,
  "compatibility_score": <integer 0-100>,
  "match_reason": "<2-3 warm, specific sentences explaining why this is a great match>",
  "conversation_starter": "<a concrete suggested first message the seeker could send>",
  "estimated_help_type": "<exactly one of: Academic, Social, Emotional, Career, Mixed>",
  "runner_up_id": <integer user_id or null>,
  "runner_up_score": <integer 0-100 or null>
}}"""

        resp = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                max_output_tokens=600,
                temperature=0.4,
            ),
        )

        raw = resp.text.strip()
        # Strip markdown code fences if Gemini added them
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        data = json.loads(raw)

        valid_ids = {h["id"] for h in helpers}
        best_id   = data.get("best_match_id")
        if best_id not in valid_ids:
            best_id = helpers[0]["id"]

        best = next((h for h in helpers if h["id"] == best_id), helpers[0])

        runner_up_id = data.get("runner_up_id")
        if runner_up_id not in valid_ids:
            runner_up_id = None

        help_type = data.get("estimated_help_type", "Mixed")
        if help_type not in HELP_TYPE_ICONS:
            help_type = "Mixed"

        return {
            "helper":               best,
            "compatibility_score":  max(0, min(100, int(data.get("compatibility_score", 75)))),
            "match_reason":         data.get("match_reason", ""),
            "conversation_starter": data.get("conversation_starter", ""),
            "estimated_help_type":  help_type,
            "runner_up_id":         runner_up_id,
            "runner_up_score":      data.get("runner_up_score"),
            "method":               "gemini",
        }

    except Exception as exc:
        print(f"[matchmaking] Gemini error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Local fallback
# ---------------------------------------------------------------------------

def _local_fallback(seeker, helpers, seeker_stress):
    """Score all helpers locally and pick the best two."""
    scored = sorted(
        helpers,
        key=lambda h: _local_score(seeker, h, seeker_stress),
        reverse=True,
    )
    best  = scored[0]
    score = _local_score(seeker, best, seeker_stress, )

    runner_up_id    = scored[1]["id"]    if len(scored) > 1 else None
    runner_up_score = _local_score(seeker, scored[1], seeker_stress) if len(scored) > 1 else None

    return {
        "helper":               best,
        "compatibility_score":  max(score, 45),  # floor at 45 for UX
        "match_reason": (
            f"{best['first_name']} has a complementary personality and is ready to help "
            f"with exactly what you're going through. We think you'll work really well together!"
        ),
        "conversation_starter": (
            f"Hey {best['first_name']}! I was just matched with you on ConnectAI. "
            f"I'd love to connect — hope we can chat soon!"
        ),
        "estimated_help_type":  "Mixed",
        "runner_up_id":         runner_up_id,
        "runner_up_score":      runner_up_score,
        "method":               "local",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_best_match(seeker_id, seeker_stress, seeker_help_offered, candidates):
    """
    Main entry point called from app.py.

    Parameters
    ----------
    seeker_id          : int — the user seeking help
    seeker_stress      : str — what they're going through
    seeker_help_offered: str — what they can offer in return
    candidates         : list of {"user": Row, "answers": [Row, ...]}
                         from get_available_users_with_surveys()

    Returns
    -------
    dict with keys: helper, compatibility_score, match_reason,
                    conversation_starter, estimated_help_type,
                    runner_up_id, runner_up_score, method
    OR None if candidates list is empty.
    """
    if not candidates:
        return None

    from database import get_survey_answers, get_user_by_id

    seeker_user    = get_user_by_id(seeker_id)
    seeker_answers = get_survey_answers(seeker_id)
    seeker_p       = build_personality(seeker_answers)

    seeker_info = {
        "id":           seeker_id,
        "name":         seeker_user["name"],
        "first_name":   seeker_user["first_name"] or seeker_user["name"].split()[0],
        "graduation":   seeker_user["graduation"] or "Unknown",
        "stress":       seeker_stress,
        "help_offered": seeker_help_offered,
        "personality":  seeker_p,
    }

    helper_profiles = []
    for c in candidates:
        u = c["user"]
        p = build_personality(c["answers"])
        # Safely read help_offered from the user row (added via migration)
        try:
            help_offered = u["help_offered"] or ""
        except (IndexError, KeyError):
            help_offered = ""

        helper_profiles.append({
            "id":            u["id"],
            "name":          u["name"],
            "first_name":    u["first_name"] or u["name"].split()[0],
            "graduation":    u["graduation"] or "Unknown",
            "email":         u["email"],
            "profile_photo": u["profile_photo"],
            "help_offered":  help_offered,
            "personality":   p,
        })

    # Try Gemini first, fall back to local scoring
    result = _try_gemini(seeker_info, helper_profiles)
    return result or _local_fallback(seeker_info, helper_profiles, seeker_stress)
