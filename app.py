from flask import Flask
from flask_cors import CORS

from routes.ingredients import ingredients_bp
from routes.flavors import flavors_bp
from routes.orders import orders_bp
from routes.menus import menus_bp



app = Flask(__name__)
CORS(app, supports_credentials=True, origins=["http://localhost:3000"])


# 註冊 Blueprint
app.register_blueprint(ingredients_bp)
app.register_blueprint(flavors_bp)
app.register_blueprint(orders_bp)
app.register_blueprint(menus_bp)

@app.route('/')
def home():
    return "紅豆餅店庫存管理系統後端運行中..."

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)

