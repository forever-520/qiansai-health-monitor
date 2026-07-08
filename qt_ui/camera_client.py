from pathlib import Path
import urllib.request


def request_image(host: str, port: int, output_path: Path, trigger: int = 0x01, count: int = 1, timeout: int = 60) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")

    url = f"http://{host}:{port}/camera/capture"
    req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"capture failed: HTTP {response.status}")
        jpeg_data = response.read()
        if not jpeg_data.startswith(b"\xff\xd8"):
            raise RuntimeError("capture failed: invalid jpeg")
        temp_path.write_bytes(jpeg_data)
        temp_path.replace(output_path)
        return output_path
