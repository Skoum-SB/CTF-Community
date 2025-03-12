import json
from flask import Blueprint, request, jsonify, render_template, url_for, redirect, Flask, flash
from CTFd.models import db
from .models import ContainerChallengeModel, ContainerInfoModel, ContainerSettingsModel
from .container_manager import ContainerManager, ContainerException
from CTFd.utils.decorators import (
    authed_only,
    admins_only,
    during_ctf_time_only,
    ratelimit,
    require_verified_emails,
)
from .helpers import *
from CTFd.utils.user import get_current_user
from CTFd.utils import get_config

containers_bp = Blueprint("container_user", __name__, url_prefix="/containers")

container_manager = None

def set_container_manager(manager):
    global container_manager
    container_manager = manager


@containers_bp.route("/api/get_connect_type/<int:challenge_id>", methods=["GET"])
@authed_only
@during_ctf_time_only
@require_verified_emails
@ratelimit(method="GET", limit=15, interval=60)
def get_connect_type(challenge_id):
    try:
        return connect_type(challenge_id)
    except ContainerException as err:
        return {"error": str(err)}, 500

@containers_bp.route("/api/view_info", methods=["POST"])
@authed_only
@during_ctf_time_only
@require_verified_emails
@ratelimit(method="POST", limit=15, interval=60)
def route_view_info():
    user = get_current_user()

    # Validate the request
    if request.json is None:
        return {"error": "Invalid request"}, 400

    if request.json.get("chal_id", None) is None:
        return {"error": "No chal_id specified"}, 400

    if user is None:
        return {"error": "User not found"}, 400
    if user.team is None and is_team_mode() is True:
        return {"error": "User not a member of a team"}, 400

    try:
        if is_team_mode() is True:
            return view_container_info(
                container_manager, request.json.get("chal_id"), user.team.id, True
            )
        elif is_team_mode() is False:
            return view_container_info(container_manager, request.json.get("chal_id"), user.id, False)
    except ContainerException as err:
        return {"error": str(err)}, 500

@containers_bp.route("/api/request", methods=["POST"])
@authed_only
@during_ctf_time_only
@require_verified_emails
@ratelimit(method="POST", limit=6, interval=60)
def route_request_container():
    user = get_current_user()

    # Validate the request
    if request.json is None:
        return {"error": "Invalid request"}, 400

    if request.json.get("chal_id", None) is None:
        return {"error": "No chal_id specified"}, 400

    if user is None:
        return {"error": "User not found"}, 400
    if user.team is None and is_team_mode() is True:
        return {"error": "User not a member of a team"}, 400

    try:
        if is_team_mode() is True:
            return create_container(container_manager, request.json.get("chal_id"), user.team.id, True)
        elif is_team_mode() is False:
            return create_container(container_manager, request.json.get("chal_id"), user.id, False)
    except ContainerException as err:
        return {"error": str(err)}, 500

@containers_bp.route("/api/renew", methods=["POST"])
@authed_only
@during_ctf_time_only
@require_verified_emails
@ratelimit(method="POST", limit=6, interval=60)
def route_renew_container():
    user = get_current_user()

    # Validate the request
    if request.json is None:
        return {"error": "Invalid request"}, 400

    if request.json.get("chal_id", None) is None:
        return {"error": "No chal_id specified"}, 400

    if user is None:
        return {"error": "User not found"}, 400
    if user.team is None and is_team_mode() is True:
        return {"error": "User not a member of a team"}, 400

    try:
        if is_team_mode() is True:
            return renew_container(container_manager, request.json.get("chal_id"), user.team.id, True)
        elif is_team_mode() is False:
            return renew_container(container_manager, request.json.get("chal_id"), user.id, False)
    except ContainerException as err:
        return {"error": str(err)}, 500

@containers_bp.route("/api/stop", methods=["POST"])
@authed_only
@during_ctf_time_only
@require_verified_emails
@ratelimit(method="POST", limit=10, interval=60)
def route_stop_container():
    user = get_current_user()

    # Validate the request
    if request.json is None:
        return {"error": "Invalid request"}, 400

    if request.json.get("chal_id", None) is None:
        return {"error": "No chal_id specified"}, 400

    if user is None:
        return {"error": "User not found"}, 400
    if user.team is None and is_team_mode() is True:
        return {"error": "User not a member of a team"}, 400

    if is_team_mode() is True:
        running_container: ContainerInfoModel = ContainerInfoModel.query.filter_by(
            challenge_id=request.json.get("chal_id"), team_id=user.team.id
        ).first()

        if running_container:
            return kill_container(container_manager, running_container.container_id)

        return {"error": "No container found"}, 400
    elif is_team_mode() is False:
        running_container: ContainerInfoModel = ContainerInfoModel.query.filter_by(
            challenge_id=request.json.get("chal_id"), user_id=user.id
        ).first()

        if running_container:
            return kill_container(container_manager, running_container.container_id)

        return {"error": "No container found"}, 400
