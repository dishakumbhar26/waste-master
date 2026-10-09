import hashlib, secrets


def hp(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()


def check(pw, h):
    return secrets.compare_digest(hp(pw, h.split("$")[0]), h)
