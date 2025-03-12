from __future__ import division

import time
import json
import datetime
import math

from flask import Blueprint, request, Flask, render_template, url_for, redirect, flash

from CTFd.models import db, Solves, Teams, Users
from CTFd.plugins import register_plugin_assets_directory
from CTFd.plugins.challenges import CHALLENGE_CLASSES, BaseChallenge
from CTFd.utils.modes import get_model
from .models import ContainerChallengeModel, ContainerInfoModel, ContainerSettingsModel, ContainerFlagModel
from .container_manager import ContainerManager, ContainerException
from .admin_routes import admin_bp, set_container_manager as set_admin_manager
from .user_routes import containers_bp, set_container_manager as set_user_manager
from .helpers import *
from CTFd.utils.user import get_current_user

settings = json.load(open(get_settings_path()))

class ContainerChallenge(BaseChallenge):
    id = settings["plugin-info"]["id"]
    name = settings["plugin-info"]["name"]
    templates = settings["plugin-info"]["templates"]
    scripts = settings["plugin-info"]["scripts"]
    route = settings["plugin-info"]["base_path"]

    challenge_model = ContainerChallengeModel

    @classmethod
    def read(cls, challenge):
        """
        This method is in used to access the data of a challenge in a format processable by the front end.

        :param challenge:
        :return: Challenge object, data dictionary to be returned to the user
        """
        data = {
            "id": challenge.id,
            "name": challenge.name,
            "value": challenge.value,
            "image": challenge.image,
            "port": challenge.port,
            "command": challenge.command,
            "connection_type": challenge.connection_type,
            "initial": challenge.initial,
            "decay": challenge.decay,
            "minimum": challenge.minimum,
            "description": challenge.description,
            "connection_info": challenge.connection_info,
            "category": challenge.category,
            "state": challenge.state,
            "max_attempts": challenge.max_attempts,
            "type": challenge.type,
            "type_data": {
                "id": cls.id,
                "name": cls.name,
                "templates": cls.templates,
                "scripts": cls.scripts,
            },
        }
        return data

    @classmethod
    def calculate_value(cls, challenge):
        Model = get_model()

        solve_count = (
            Solves.query.join(Model, Solves.account_id == Model.id)
            .filter(
                Solves.challenge_id == challenge.id,
                Model.hidden == False,
                Model.banned == False,
            )
            .count()
        )

        # If the solve count is 0 we shouldn't manipulate the solve count to
        # let the math update back to normal
        if solve_count != 0:
            # We subtract -1 to allow the first solver to get max point value
            solve_count -= 1

        # It is important that this calculation takes into account floats.
        # Hence this file uses from __future__ import division
        value = (
            ((challenge.minimum - challenge.initial) / (challenge.decay**2))
            * (solve_count**2)
        ) + challenge.initial

        value = math.ceil(value)

        if value < challenge.minimum:
            value = challenge.minimum

        challenge.value = value
        db.session.commit()
        return challenge

    @classmethod
    def update(cls, challenge, request):
        """
        This method is used to update the information associated with a challenge. This should be kept strictly to the
        Challenges table and any child tables.
        :param challenge:
        :param request:
        :return:
        """
        data = request.form or request.get_json()

        for attr, value in data.items():
            # We need to set these to floats so that the next operations don't operate on strings
            if attr in ("initial", "minimum", "decay"):
                value = float(value)
            setattr(challenge, attr, value)

        return ContainerChallenge.calculate_value(challenge)

    @classmethod
    def solve(cls, user, team, challenge, request):
        super().solve(user, team, challenge, request)

        cls.calculate_value(challenge)

    @classmethod
    def attempt(cls, challenge, request):
        """
        Overridden attempt method which CTFd calls automatically
        when a user submits a flag for this challenge.

        Returns:
            (True/False, message_string)
        """
        # Grab the current user
        user = get_current_user()
        if user is None:
            return False, "You must be logged in to attempt this challenge."

        # If your CTF is in team mode, you must have a valid team
        if is_team_mode():
            if not user.team_id:
                return False, "You must belong to a team to solve this challenge."
            x_id = user.team_id
        else:
            x_id = user.id

        data = request.get_json()
        # 2) If there's no JSON, fall back to form data (just in case)
        if not data:
            data = request.form

        submitted_flag = data.get("submission", "").strip()
        if not submitted_flag:
            return False, "No flag provided. a"

        # Pull the user's (or team's) running container, if any, for this challenge
        container_info = ContainerInfoModel.query.filter_by(
            challenge_id=challenge.id,
            team_id=x_id if is_team_mode() else None,
            user_id=None if is_team_mode() else x_id,
        ).first()

        # If no container or container is not running, block the solve
        if not container_info:
            return False, "No container is currently active for this challenge."
        from . import container_manager  # if your ContainerManager instance is importable
        if not container_manager or not container_manager.is_container_running(container_info.container_id):
            return False, "Your container is not running; you cannot submit yet."

        # Look in ContainerFlagModel for a row that matches this flag
        container_flag = ContainerFlagModel.query.filter_by(flag=submitted_flag).first()

        # If that flag doesn't exist in the container_flags table, reject
        if not container_flag:
            return False, "Incorrect flag."

        # If this flag was already used, treat this as cheating => ban user or team
        if container_flag.used and challenge.flag_mode == "random":
            # Flag has already been used => cheating
            if is_team_mode():
                # 1) Ban the "original" team that had this flag
                original_team_id = container_flag.team_id
                if original_team_id:
                    original_team = Teams.query.filter_by(id=original_team_id).first()
                    if original_team:
                        original_team.banned = True
                        for member in original_team.members:
                            member.banned = True

                # 2) Ban the "submitting" team that just tried to reuse the flag
                submit_team_id = user.team_id
                if submit_team_id:
                    submit_team = Teams.query.filter_by(id=submit_team_id).first()
                    if submit_team:
                        submit_team.banned = True
                        for member in submit_team.members:
                            member.banned = True

            else:
                # User mode => ban both the original user and the new user
                if container_flag.user_id:
                    original_user = Users.query.filter_by(id=container_flag.user_id).first()
                    if original_user:
                        original_user.banned = True

                user.banned = True

            db.session.commit()
            container_manager.kill_container(container_info.container_id)
            return False, "Cheating detected. Both teams have been banned."

        # Otherwise, mark the container_flag as used and accept it
        container_flag.used = True
        db.session.commit()
        container_manager.kill_container(container_info.container_id)

        # If we get here, it's correct and not used before => success
        return True, "Correct!"

container_manager = None  # Global

def load(app: Flask):
    # Ensure database is initialized
    app.db.create_all()

    # Register the challenge type
    CHALLENGE_CLASSES["container"] = ContainerChallenge

    register_plugin_assets_directory(
        app, base_path=settings["plugin-info"]["base_path"]
    )

    global container_manager
    container_settings = settings_to_dict(ContainerSettingsModel.query.all())
    container_manager = ContainerManager(container_settings, app)

    base_bp = Blueprint(
        "containers",
        __name__,
        template_folder=settings["blueprint"]["template_folder"],
        static_folder=settings["blueprint"]["static_folder"]
    )

    set_admin_manager(container_manager)
    set_user_manager(container_manager)

    # Register the blueprints
    app.register_blueprint(admin_bp)  # Admin APIs
    app.register_blueprint(containers_bp) # User APIs


    app.register_blueprint(base_bp)
