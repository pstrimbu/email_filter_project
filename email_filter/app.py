from email_filter import create_app
from . import db

app = create_app()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host="0.0.0.0", port=5001, debug=True)
