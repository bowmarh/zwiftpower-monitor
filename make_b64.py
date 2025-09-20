
import base64

with open("storage_state.json", "rb") as f:
    data = f.read()

encoded = base64.b64encode(data).decode("utf-8")

with open("storage_state.b64", "w") as f:
    f.write(encoded)

print("✅ Saved base64 to storage_state.b64")
