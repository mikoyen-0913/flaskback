import firebase_admin
from firebase_admin import credentials, firestore

cred = credentials.Certificate("yaoyaoproject-88907-firebase-adminsdk-fbsvc-e65f9829cc.json")
firebase_admin.initialize_app(cred)
db = firestore.client()
