from flask import Flask, request, jsonify
import os
import re
import json
from datetime import datetime
from transformers import AutoModelForCausalLM, AutoTokenizer

# Initialize Flask app
app = Flask(__name__)

# Load model and tokenizer
MODEL_NAME = "INSERT_MODEL_HERE"
model = AutoModelForCausalLM.from_pretrained(MODEL_NAME, torch_dtype="auto", device_map="auto")
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

# Define allowed keys directory
ALLOWED_KEYS_DIR = "./allowed"

# Master key for privileged endpoints — read from the environment, never hardcoded.
# Fail fast at startup if it's missing, so an unset key can't collapse to None and
# turn the /update_params check into a None == None auth bypass.
MASTER_KEY = os.getenv("MASTER_KEY")
if not MASTER_KEY:
    raise RuntimeError("MASTER_KEY environment variable is not set.")

def is_safe_key(api_key):
    """Only allow simple keys (letters, numbers, _ and -).

    Blocks anything else (like '/' or '..') so a key can never be used to
    escape ALLOWED_KEYS_DIR and reach other files on disk (path traversal).
    """
    return bool(api_key) and re.fullmatch(r"[A-Za-z0-9_-]+", api_key) is not None

def load_key_data(api_key):
    """Load API key data from JSON file."""
    if not is_safe_key(api_key):
        return None
    key_path = os.path.join(ALLOWED_KEYS_DIR, f"{api_key}.json")
    if os.path.exists(key_path):
        with open(key_path, 'r') as f:
            return json.load(f)
    return None

def save_key_data(api_key, data):
    """Save API key data to JSON file."""
    if not is_safe_key(api_key):
        return
    key_path = os.path.join(ALLOWED_KEYS_DIR, f"{api_key}.json")
    with open(key_path, 'w') as f:
        json.dump(data, f, indent=4)

def update_key_usage(api_key, ip):
    """Update API key usage count and IPs."""
    key_data = load_key_data(api_key)
    if key_data:
        key_data["Usage Times"] += 1
        if ip not in key_data["IPs"]:
            key_data["IPs"].append(ip)
        save_key_data(api_key, key_data)

def is_valid_key(api_key):
    """Check if the provided API key is valid."""
    if not is_safe_key(api_key):
        return False
    return os.path.exists(os.path.join(ALLOWED_KEYS_DIR, f"{api_key}.json"))

# Default generation parameters, change here.
generation_params = {
    "max_new_tokens": 512
}

@app.route("/generate", methods=["POST"])
def generate():
    api_key = request.headers.get("Authorization")
    client_ip = request.remote_addr

    # Validate API key
    if not api_key or not is_valid_key(api_key):
        return jsonify({"error": "Invalid or missing API key."}), 403

    # Parse input
    try:
        data = request.json
        messages = data["messages"]
    except (KeyError, TypeError):
        return jsonify({"error": "Invalid input format. Expected 'messages' field."}), 400

    # Update usage stats
    update_key_usage(api_key, client_ip)

    # Prepare the input for the model
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    model_inputs = tokenizer([text], return_tensors="pt").to(model.device)

    # Generate response
    generated_ids = model.generate(**model_inputs, **generation_params)
    generated_ids = [
        output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
    ]
    response = tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]

    return jsonify({"response": response})

@app.route("/update_params", methods=["POST"])
def update_params():
    api_key = request.headers.get("Authorization")

    # Validate master key
    if api_key != MASTER_KEY:
        return jsonify({"error": "Unauthorized. Only MASTER key can update parameters."}), 403

    # Parse input
    try:
        new_params = request.json
    except (KeyError, TypeError):
        return jsonify({"error": "Invalid input format."}), 400

    # Update generation parameters
    global generation_params
    generation_params.update(new_params)

    return jsonify({"message": "Generation parameters updated successfully.", "new_params": generation_params})

@app.route("/key_info/<api_key>", methods=["GET"])
def key_info(api_key):
    api_key = api_key.strip()

    # Validate key existence
    key_data = load_key_data(api_key)
    if not key_data:
        return jsonify({"error": "API key not found."}), 404

    return jsonify(key_data)

if __name__ == "__main__":
    if not os.path.exists(ALLOWED_KEYS_DIR):
        os.makedirs(ALLOWED_KEYS_DIR)

    app.run(debug=False, port=5000, host='0.0.0.0')
