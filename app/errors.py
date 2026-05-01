import logging

from flask import jsonify, render_template, request


logger = logging.getLogger(__name__)


def register_error_handlers(app):
    @app.errorhandler(400)
    def bad_request(error):
        logger.warning("Bad request: %s", error)
        if request.path.startswith("/api"):
            return jsonify({"error": "Bad request"}), 400
        return render_template("error.html", message="Bad request."), 400

    @app.errorhandler(403)
    def forbidden(error):
        logger.warning("Forbidden: %s", error)
        return render_template("error.html", message="Forbidden."), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", message="Page not found."), 404

    @app.errorhandler(413)
    def payload_too_large(error):
        return render_template("error.html", message="Uploaded file is too large."), 413

    @app.errorhandler(429)
    def too_many_requests(error):
        return render_template("error.html", message="Rate limit exceeded. Try later."), 429

    @app.errorhandler(500)
    def server_error(error):
        logger.exception("Unhandled server error: %s", error)
        return render_template("error.html", message="Unexpected server error."), 500
