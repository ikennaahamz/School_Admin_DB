"""
Temporary screenshot harness. Not part of the deliverable.

Streamlit screenshots need a signed-in session, and a plain headless
browser cannot type into the login form. This renders each real screen
function with an authenticated user so Chrome can capture it.

    streamlit run scripts/_screenshot_harness.py --server.port 8502

Then capture with:
    chrome --headless --screenshot=out.png --window-size=1600,1000
           "http://localhost:8502/?s=students"

Delete this file once the screenshots are taken.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from src import auth, screens

st.set_page_config(page_title="School Administration System",
                   page_icon="🎓", layout="wide")

SCREENS = {
    "dashboard": screens.dashboard,
    "students": screens.students_crud,
    "instructors": screens.instructors_crud,
    "sections": screens.sections_crud,
    "enrollments": screens.enrollments_crud,
    "assignments": screens.assignments_crud,
    "attendance": screens.attendance_crud,
    "reports": screens.reports_screen,
    "admin": screens.admin_users,
}

user, _ = auth.authenticate("admin", "Passw0rd!")

which = st.query_params.get("s", "dashboard")
if user is None:
    st.error("Harness could not authenticate.")
else:
    st.sidebar.markdown(f"### 🎓 {user.full_name}")
    st.sidebar.caption(f"`{user.username}` · level {user.access_level}")
    for role in user.roles:
        st.sidebar.caption(f"• {role}")
    st.sidebar.markdown("---")
    st.sidebar.radio("Navigate", list(SCREENS),
                     index=list(SCREENS).index(which) if which in SCREENS else 0,
                     label_visibility="collapsed")
    SCREENS[which if which in SCREENS else "dashboard"](user)