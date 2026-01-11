from flask import jsonify

from dispatcher import handler


def event(request):
    return jsonify(handler(request.get_json()))
