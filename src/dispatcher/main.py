from dispatcher import handler
from flask import jsonify

def event(request):
    return jsonify(handler(request.get_json()))