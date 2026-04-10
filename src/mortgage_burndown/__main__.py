"""Run the Flask app: python -m mortgage_burndown"""

from mortgage_burndown.app import app

if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
