import firebase_admin
from firebase_admin import credentials, firestore

cred = credentials.Certificate("yaoyaoproject-88907-firebase-adminsdk-fbsvc-b498692948.json")
firebase_admin.initialize_app(cred)
db = firestore.client()
