# Python (Chaquopy) calls these Kotlin members by name at runtime; R8 must not rename or strip them.
-keep class app.friendsend.friendsend.VideoDownloader$Control { *; }
