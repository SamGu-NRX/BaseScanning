"""Device presets per noise profile. The hardware identifier is what `utsname.machine` returns."""

DEVICES = {
    "lidar_modern": {
        "deviceModel": "iPhone16,1",
        "marketingName": "iPhone 15 Pro",
        "systemVersion": "26.0",
        "lidarAvailable": True,
        "meshReconstructionSupported": True,
        "lens": "iPhone 15 Pro back triple camera 6.765mm f/1.78",
        "fNumber": 1.78,
        "focalLengthMm": 6.765,
        "arkitExifData": True,
    },
    "nonlidar_modern": {
        "deviceModel": "iPhone15,4",
        "marketingName": "iPhone 15",
        "systemVersion": "26.0",
        "lidarAvailable": False,
        "meshReconstructionSupported": False,
        "lens": "iPhone 15 back dual wide camera 6.24mm f/1.6",
        "fNumber": 1.6,
        "focalLengthMm": 6.24,
        "arkitExifData": True,
    },
    "nonlidar_2018": {
        "deviceModel": "iPhone8,1",
        "marketingName": "iPhone 6s",
        "systemVersion": "15.8.3",
        "lidarAvailable": False,
        "meshReconstructionSupported": False,
        "lens": "iPhone 6s back camera 4.15mm f/2.2",
        "fNumber": 2.2,
        "focalLengthMm": 4.15,
        # ARFrame.exifData needs iOS 16; the 6s stops at iOS 15.
        "arkitExifData": False,
    },
}

VARIANTS = {
    "arkit-lidar": {"profile": "lidar_modern", "tier": "arkit_lidar"},
    "arkit-nonlidar": {"profile": "nonlidar_modern", "tier": "arkit"},
    "arkit-nonlidar-2018": {"profile": "nonlidar_2018", "tier": "arkit"},
    "pose-free": {"profile": "nonlidar_modern", "tier": "photo_only"},
}
