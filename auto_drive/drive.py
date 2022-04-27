import base64
import threading
from io import BytesIO
import time

import cv2
import eventlet
import numpy as np
from flask import (Flask, redirect, request,
                   send_from_directory, jsonify)
from flask_socketio import SocketIO
from PIL import Image
from werkzeug import debug

from controller import *
from traffic_sign_detection import *
from image_stream import image_streamer
from utils import *
from multiprocessing import Process, Queue

eventlet.monkey_patch()

app = Flask(__name__, static_url_path='')
sio = SocketIO(app)

g_image_queue = Queue(maxsize=5)
sign_queue = Queue(maxsize=5)

traffic_sign_model = cv2.dnn.readNetFromONNX("traffic_sign_classifier_lenet_v3.onnx")
def process_traffic_sign_loop(g_image_queue, sign_queue):
    while True:
            if g_image_queue.empty():
                time.sleep(0.1)
                continue
            image = g_image_queue.get()
            # Prepare visualization image
            draw = image.copy()
            # Detect traffic signs
            signs, sign_vector = detect_traffic_signs(image, traffic_sign_model, draw=None)
            if not sign_queue.full():
                sign_queue.put(sign_vector)
            # Show the result to a window
            cv2.imshow("Traffic signs", draw)
            cv2.waitKey(1)
@sio.on('telemetry')
def telemetry(data):
    global debug_images

    if data:
        throttle = float(data["throttle"])
        steering_angle = float(data["steering_angle"])
        current_speed = float(data["speed"])
        image = Image.open(BytesIO(base64.b64decode(data["image"])))
        #try:
        image = np.asarray(image)
        image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        image_streamer.set_image("rgb", image)

        sign_vector = [0, 0, 0, 0, 0, 0, 0]
        if not sign_queue.empty():
            sign_vector = sign_queue.get()

        # Calculate speed and steering angle
        throttle, steering_angle = calculate_control_signal(
            current_speed, sign_vector, image.copy())

        if not g_image_queue.full():
            g_image_queue.put(image)

        send_control(sio, steering_angle, throttle)
        #except Exception as e:
        #    print(e)
    else:
        sio.emit('manual', data={}, skip_sid=True)


@app.route('/')
def homepage():
    return redirect("/web/index.html?t={}".format(time.time()))

@app.route('/web/<path:path>')
def send_web(path):
    return send_from_directory('web', path)

@sio.on('connect')
def connect():
    send_control(sio, 0, 0)
    print('[INFO] Client connected: {}'.format(request.sid))

@sio.on('disconnect')
def disconnect():
    print('[INFO] Client disconnected: {}'.format(request.sid))

@app.route('/api/get_topics')
def get_topics():
    return jsonify({
        "success": True,
        "topics": image_streamer.get_topics()
    })

@app.route('/api/set_topic')
def set_topic():
    topic = request.args.get("topic", "")
    ret, message = image_streamer.set_current_topic(topic)
    if ret:
        return jsonify({
            "success": True,
            "new_topic": topic
        })
    else:
        return jsonify({
            "success": False,
            "message": message
        })

def info_thread_func(sio):
    global count
    while True:
        sio.sleep(0.05)
        frame = image_streamer.get_image(image_streamer.get_current_topic())
        sio.emit(
            'server2web',
            {
                'image': convert_image_to_jpeg(frame),
                'topic': image_streamer.get_current_topic()
            },
            skip_sid=True, broadcast=True)

# Start info streaming thread
if __name__ == '__main__':
    info_thread = threading.Thread(target=info_thread_func, args=(sio,))
    info_thread.setDaemon(True)
    info_thread.start()

    p = Process(target=process_traffic_sign_loop, args=(g_image_queue, sign_queue))
    p.start()


    print("Starting server. Go to: http://localhost:4567")
    sio.run(app, port=4567)
