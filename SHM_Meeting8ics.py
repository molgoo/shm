import streamlit as st
from sqlalchemy import create_engine, text
import pandas as pd
from st_aggrid import AgGrid, GridUpdateMode, GridOptionsBuilder
import sqlite3
import icalendar
from io import StringIO
from datetime import datetime
import re

# Database Configuration
DB_PATH = 'shm1.db'


# Database Operations
class DatabaseOperations:
    @staticmethod
    def get_connection():
        return sqlite3.connect(DB_PATH)

    @staticmethod
    def fetch_data(query, conn):
        return pd.read_sql_query(query, conn)

    @staticmethod
    def fetch_data_with_params(query, conn, params):
        return pd.read_sql_query(query, conn, params=params)

    @staticmethod
    def execute_query(query, conn, params=None):
        cursor = conn.cursor()
        cursor.execute(query, params or ())
        conn.commit()


# ICS File Operations
class ICSOperations:
    @staticmethod
    def parse_attendee_email(attendee_string):
        email_match = re.search(r'mailto:(.*)', attendee_string)
        if email_match:
            return email_match.group(1)
        return None

    @staticmethod
    def parse_attendee_name(description):
        name_parts = description.split(':')[-1].split(' ', 1)
        if len(name_parts) >= 2:
            return name_parts[0], name_parts[1]
        return name_parts[0], ""

    @staticmethod
    def parse_ics(file):
        try:
            calendar = icalendar.Calendar.from_ical(file.read())
            meeting_details = {}
            for component in calendar.walk():
                if component.name == "VEVENT":
                    meeting_details['title'] = str(component.get('summary', ''))
                    meeting_details['date'] = component.get('dtstart').dt
                    meeting_details['description'] = str(component.get('description', ''))

                    attendees = []
                    for attendee in component.get('attendee', []):
                        email = ICSOperations.parse_attendee_email(str(attendee))
                        if email:
                            cn = attendee.params.get('CN', '')
                            if cn:
                                first_name, last_name = ICSOperations.parse_attendee_name(cn)
                            else:
                                first_name = email.split('@')[0]
                                last_name = ""

                            attendees.append({
                                'email': email,
                                'first_name': first_name,
                                'last_name': last_name
                            })

                    meeting_details['attendees'] = attendees
                    break
            return meeting_details
        except Exception as e:
            st.error(f"Error parsing ICS file: {e}")
            return None


# Stakeholder Operations
class StakeholderOperations:
    @staticmethod
    def add_stakeholder_if_not_exists(conn, email, first_name, last_name):
        check_query = "SELECT pk_sk_id FROM Stakeholders WHERE email = ?"
        cursor = conn.cursor()
        cursor.execute(check_query, (email,))
        result = cursor.fetchone()

        if result:
            return result[0]

        insert_query = """
        INSERT INTO Stakeholders (first_name, last_name, email)
        VALUES (?, ?, ?)
        """
        cursor.execute(insert_query, (first_name, last_name, email))
        conn.commit()
        return cursor.lastrowid

    @staticmethod
    def format_stakeholder_name(stakeholder_id, stakeholders):
        if stakeholders.empty:
            return f"Stakeholder {stakeholder_id}"
        matching_rows = stakeholders[stakeholders['pk_sk_id'] == stakeholder_id]
        if matching_rows.empty:
            return f"Unknown Stakeholder {stakeholder_id}"
        first_name = matching_rows['first_name'].iloc[0]
        last_name = matching_rows['last_name'].iloc[0]
        return f"{first_name} {last_name}".strip() or f"Stakeholder {stakeholder_id}"


# Meeting Management UI
class MeetingUI:
    def __init__(self, conn):
        self.conn = conn
        self.db_ops = DatabaseOperations()

    def create_meeting_ui(self):
        # Define queries
        stakeholders_query = "SELECT pk_sk_id, first_name, last_name, email FROM Stakeholders"
        stakeholders = self.db_ops.fetch_data(stakeholders_query, self.conn)

        # Initialize new_meeting_stakeholders
        if 'new_meeting_stakeholders' not in locals():
            new_meeting_stakeholders = []

        # Input fields for new meeting
        new_meeting_title = st.text_input("Meeting Title")
        new_meeting_date = st.date_input("Meeting Date")
        new_meeting_discussion = st.text_area("Meeting Discussion Notes")

        # Option to upload .ics file
        ics_file = st.file_uploader("Upload Outlook Meeting (.ics file)", type="ics")

        if ics_file is not None:
            meeting_data = ICSOperations.parse_ics(ics_file)
            if meeting_data:
                new_meeting_title = meeting_data.get('title', '')
                new_meeting_date = meeting_data.get('date', datetime.now()).date()
                new_meeting_discussion = meeting_data.get('description', '')

                # Process attendees
                attendees = meeting_data.get('attendees', [])
                new_meeting_stakeholders = []

                # Add each attendee
                for attendee in attendees:
                    stakeholder_id = StakeholderOperations.add_stakeholder_if_not_exists(
                        self.conn,
                        attendee['email'],
                        attendee['first_name'],
                        attendee['last_name']
                    )
                    if stakeholder_id:
                        new_meeting_stakeholders.append(stakeholder_id)
                        st.write(
                            f"Added/Found stakeholder ID: {stakeholder_id} for {attendee['first_name']} {attendee['last_name']}")

                if 'ics_stakeholders' not in st.session_state:
                    st.session_state.ics_stakeholders = []
                st.session_state.ics_stakeholders = new_meeting_stakeholders

                st.write(f"Found {len(attendees)} attendees in the meeting invitation")
                stakeholders = self.db_ops.fetch_data(stakeholders_query, self.conn)

        # Get stakeholders from session state
        if 'ics_stakeholders' not in st.session_state:
            st.session_state.ics_stakeholders = []

        # Show multiselect if there are stakeholders
        if not stakeholders.empty:
            available_stakeholder_ids = set(stakeholders['pk_sk_id'].tolist())
            default_selections = list(set(st.session_state.ics_stakeholders))
            valid_defaults = [sid for sid in default_selections if sid in available_stakeholder_ids]

            selected_stakeholders = st.multiselect(
                "Select Stakeholders for the New Meeting:",
                options=stakeholders['pk_sk_id'].tolist(),
                default=valid_defaults,
                format_func=lambda x: StakeholderOperations.format_stakeholder_name(x, stakeholders)
            )
        else:
            st.warning("No stakeholders available. Please add stakeholders first.")
            selected_stakeholders = []

        if st.button("Add Meeting"):
            cursor = self.conn.cursor()
            try:
                # Insert new meeting
                cursor.execute("""
                    INSERT INTO Meetings (title, date, discussions)
                    VALUES (?, ?, ?)
                """, (new_meeting_title, new_meeting_date, new_meeting_discussion))
                self.conn.commit()

                new_meeting_id = cursor.lastrowid
                st.write(f"Debug: New meeting ID is: {new_meeting_id}")

                # Use stored stakeholders from .ics file
                ics_stakeholders = st.session_state.ics_stakeholders

                # Combine stakeholders
                all_stakeholders = list(set(selected_stakeholders + ics_stakeholders))

                # Add stakeholders to junction table
                for stakeholder_id in all_stakeholders:
                    cursor.execute("""
                        INSERT INTO Stakeholders_Meetings (fk_sh_id, fk_meeting_id)
                        VALUES (?, ?)
                    """, (stakeholder_id, new_meeting_id))
                    self.conn.commit()

                st.success("New meeting added successfully!")

                # Update session state
                st.session_state.selected_meeting_id = new_meeting_id
                st.session_state.create_new_meeting = False

                # Force a rerun to switch to the view mode
                st.rerun()

            except sqlite3.IntegrityError as e:
                st.error(f"Database integrity error: {e}")
            except Exception as e:
                st.error(f"An error occurred: {e}")
            finally:
                cursor.close()

    def view_meeting_ui(self, meetings):
        if 'selected_meeting_id' not in st.session_state:
            st.session_state.selected_meeting_id = None

        default_index = 0
        if st.session_state.selected_meeting_id is not None:
            meeting_ids = meetings['pk_meeting_id'].tolist()
            if st.session_state.selected_meeting_id in meeting_ids:
                default_index = meeting_ids.index(st.session_state.selected_meeting_id)

        meeting_id = st.selectbox(
            "Select a Meeting:",
            meetings['pk_meeting_id'],
            index=default_index,
            format_func=lambda
                x: f"{meetings.loc[meetings['pk_meeting_id'] == x, 'title'].values[0]} ({meetings.loc[meetings['pk_meeting_id'] == x, 'date'].values[0]})"
        )

        if meeting_id:
            self.handle_meeting_details(meeting_id)

        return meeting_id

    def handle_meeting_details(self, meeting_id):
        stakeholders_query = "SELECT pk_sk_id, first_name, last_name, email FROM Stakeholders"
        stakeholders = self.db_ops.fetch_data(stakeholders_query, self.conn)

        current_stakeholders_query = """
        SELECT fk_sh_id FROM Stakeholders_Meetings WHERE fk_meeting_id = ?
        """
        current_stakeholders = self.db_ops.fetch_data_with_params(
            current_stakeholders_query, self.conn, params=(meeting_id,))
        current_stakeholder_ids = current_stakeholders['fk_sh_id'].tolist() if not current_stakeholders.empty else []

        selected_stakeholders = st.multiselect(
            "Select Stakeholders:",
            stakeholders['pk_sk_id'].tolist(),
            default=current_stakeholder_ids,
            format_func=lambda x: StakeholderOperations.format_stakeholder_name(x, stakeholders)
        )

        discussions_query = "SELECT discussions FROM Meetings WHERE pk_meeting_id = ?"
        current_discussions = self.db_ops.fetch_data_with_params(
            discussions_query, self.conn, params=(meeting_id,))
        discussion_text = st.text_area(
            "Meeting Discussion Notes:",
            value=current_discussions['discussions'].iloc[0] if not current_discussions.empty else ""
        )

        if st.button("Save"):
            self.save_meeting_details(meeting_id, discussion_text, selected_stakeholders)

        # Display Selected Stakeholders (AgGrid)
        if selected_stakeholders:
            selected_data = stakeholders[stakeholders['pk_sk_id'].isin(selected_stakeholders)]
            AgGrid(selected_data)

    def save_meeting_details(self, meeting_id, discussion_text, selected_stakeholders):
        try:
            cursor = self.conn.cursor()

            # Update discussions
            cursor.execute("""
                UPDATE Meetings
                SET discussions = ?
                WHERE pk_meeting_id = ?
            """, (discussion_text, meeting_id))

            # Update stakeholders
            cursor.execute("DELETE FROM Stakeholders_Meetings WHERE fk_meeting_id = ?", (meeting_id,))

            for stakeholder_id in selected_stakeholders:
                cursor.execute("""
                    INSERT INTO Stakeholders_Meetings (fk_sh_id, fk_meeting_id)
                    VALUES (?, ?)
                """, (stakeholder_id, meeting_id))

            self.conn.commit()
            st.success("Meeting details updated successfully!")

        except Exception as e:
            st.error(f"An error occurred: {e}")
        finally:
            cursor.close()


def initialize_session_state():
    if 'create_new_meeting' not in st.session_state:
        st.session_state.create_new_meeting = False
    if 'selected_meeting_id' not in st.session_state:
        st.session_state.selected_meeting_id = None
    if 'ics_stakeholders' not in st.session_state:
        st.session_state.ics_stakeholders = []


def main():
    st.title("Meeting Management")

    # Initialize database connection
    conn = DatabaseOperations.get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")
    conn.commit()

    try:
        # Initialize session state
        initialize_session_state()

        # Create UI instance
        meeting_ui = MeetingUI(conn)

        # Fetch meetings
        meetings_query = "SELECT pk_meeting_id, title, date FROM Meetings"
        meetings = DatabaseOperations.fetch_data(meetings_query, conn)

        # Create/edit meeting toggle
        create_new_meeting = st.checkbox("Create a new meeting", value=st.session_state.create_new_meeting)
        st.session_state.create_new_meeting = create_new_meeting

        if create_new_meeting:
            meeting_ui.create_meeting_ui()
        else:
            # Meeting Selection
            if not meetings.empty:
                meeting_ui.view_meeting_ui(meetings)
            else:
                st.warning("No meetings available. Please create a new meeting.")

    finally:
        # Close database connection
        conn.close()


if __name__ == "__main__":
    main()