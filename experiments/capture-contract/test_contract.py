"""Known-answer controls. These verify adapters, not device measurement accuracy."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from capture_contract import (camera_pose, crop_resize_intrinsics,
                              no_duplicates, ray_for_pixel, validate_session, write_result)

ROOT = Path(__file__).parent

class CameraContractTests(unittest.TestCase):
    def setUp(self):
        self.session = json.loads((ROOT/"fixtures/synthetic-session.json").read_text())

    def test_translated_off_axis_ray_and_rotated_center_ray(self):
        result = validate_session(self.session)
        self.assertEqual([f["id"] for f in result["keyframes"]], ["k00001","k00002"])
        a, b = result["keyframes"]
        self.assertEqual(a["camera_to_world"],
                         [[1,0,0,0],[0,-1,0,2],[0,0,-1,3],[0,0,0,1]])
        self.assertEqual(b["camera_to_world"],
                         [[0,0,-1,2],[0,-1,0,1],[-1,0,0,5],[0,0,0,1]])
        # Independently supplied world targets must project to known image pixels.
        for frame, point, expected in [(a,[1,1,1],[750,650]), (b,[0,1,5],[500,400])]:
            matrix = frame["camera_to_world"]
            delta = [point[i]-matrix[i][3] for i in range(3)]
            camera = [sum(delta[i]*matrix[i][j] for i in range(3)) for j in range(3)]
            self.assertGreater(camera[2],0)
            k = frame["intrinsics"]
            pixel = [k[0][0]*camera[0]/camera[2]+k[0][2],
                     k[1][1]*camera[1]/camera[2]+k[1][2]]
            self.assertEqual(pixel,expected)
        self.assertFalse(b["tracking_normal"])
        self.assertFalse(result["world_frame"]["meter_anchored"])

    def test_crop_and_resize_preserve_a_known_ray(self):
        original = self.session["keyframes"][1]
        processed = deepcopy(original)
        processed["intrinsics"] = crop_resize_intrinsics(
            original["intrinsics"], [100,50], [.5,.25])
        processed["w"], processed["h"] = 450, 175
        self.assertEqual(processed["intrinsics"],[250,125,200,87.5])
        # Original (750,650) becomes ((750-100)/2,(650-50)/4).
        self.assertEqual(ray_for_pixel(original,[750,650]),
                         ray_for_pixel(processed,[325,150]))

    def test_wrong_intrinsics_are_detected_by_saved_tap(self):
        self.session["keyframes"][1]["intrinsics"][0] *= .5
        with self.assertRaisesRegex(ValueError,"saved ray disagrees"):
            validate_session(self.session)

    def test_axis_flip_or_inverted_pose_is_detected(self):
        for pose in [
            [1,0,0,0,0,-1,0,0,0,0,-1,0,0,2,3,1],  # CV basis mislabeled AR
            [1,0,0,0,0,1,0,0,0,0,1,0,0,-2,-3,1], # world-to-camera mislabeled
        ]:
            session=deepcopy(self.session)
            session["keyframes"][1]["pose"]=pose
            with self.assertRaisesRegex(ValueError,"saved ray disagrees"):
                validate_session(session)

    def test_row_major_pose_is_rejected(self):
        p=[1,0,0,0,0,1,0,2,0,0,1,3,0,0,0,1]
        with self.assertRaisesRegex(ValueError,"column-major"):
            camera_pose(p,"pose")

    def test_reflection_and_nonrigid_scale_are_rejected(self):
        for multiplier in [-1,2]:
            pose=self.session["keyframes"][1]["pose"][:]
            pose[0]=multiplier
            with self.assertRaisesRegex(ValueError,"proper rigid"):
                camera_pose(pose,"pose")

    def test_unrecognized_source_contract_and_units_are_rejected(self):
        for field,value in [("formatVersion",3),("format","other")]:
            session=deepcopy(self.session);session[field]=value
            with self.assertRaises(ValueError):validate_session(session)
        self.session["units"]["length"]="feet"
        with self.assertRaisesRegex(ValueError,"explicit meters"):
            validate_session(self.session)

    def test_duplicate_frames_and_missing_references_are_rejected(self):
        duplicate=deepcopy(self.session)
        duplicate["keyframes"].append(duplicate["keyframes"][0])
        with self.assertRaisesRegex(ValueError,"duplicate keyframe"):
            validate_session(duplicate)
        self.session["taps"][0]["keyframe"]="not-present"
        with self.assertRaisesRegex(ValueError,"unknown keyframe"):
            validate_session(self.session)

    def test_nan_boolean_and_zero_intrinsics_are_rejected(self):
        for invalid in [float("nan"),float("inf"),True,0]:
            session=deepcopy(self.session)
            session["keyframes"][1]["intrinsics"][0]=invalid
            with self.assertRaises(ValueError):validate_session(session)

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError,"duplicate JSON"):
            json.loads('{"format": 1, "format": 2}',object_pairs_hook=no_duplicates)

    def test_taps_outside_saved_image_are_rejected(self):
        for pixel in [[-100,10000],[-1,400],[500,801],[1000,400],[500,800]]:
            session=deepcopy(self.session)
            session["taps"][0]["pixel"]=pixel
            with self.assertRaisesRegex(ValueError,"outside its saved image"):
                validate_session(session)

    def test_extreme_focal_length_cannot_turn_a_ray_into_zero(self):
        self.session["keyframes"][1]["intrinsics"][0]=1e-200
        self.session["taps"][0]["rayDirection"]=[0,0,0]
        with self.assertRaisesRegex(ValueError,"unit vector"):
            validate_session(self.session)
        self.session["keyframes"][1]["intrinsics"][0]=1e-320
        with self.assertRaisesRegex(ValueError,"finite and nonzero"):
            validate_session(self.session)

    def test_integer_overflow_is_a_validation_error(self):
        self.session["keyframes"][1]["intrinsics"][0]=10**400
        with self.assertRaisesRegex(ValueError,"floating-point range"):
            validate_session(self.session)

    def test_missing_and_duplicate_tap_ids_are_rejected(self):
        for invalid in [None, "", 1]:
            session = deepcopy(self.session)
            session["taps"][0]["id"] = invalid
            with self.assertRaisesRegex(ValueError, "missing id"):
                validate_session(session)
        self.session["taps"].append(deepcopy(self.session["taps"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate tap id"):
            validate_session(self.session)

    def test_resizing_overflow_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "resized intrinsics.*finite"):
            crop_resize_intrinsics([1e200, 1, 0, 0], [0, 0], [1e200, 1])

    def test_failed_output_write_preserves_previous_result(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "converted.json"
            out.write_text("previous result")
            with patch("capture_contract.os.fsync", side_effect=OSError("disk full")):
                with self.assertRaisesRegex(OSError, "disk full"):
                    write_result(out, {"new": "result"})
            self.assertEqual(out.read_text(), "previous result")
            self.assertEqual(list(Path(temp).iterdir()), [out])
            write_result(out, {"new": "result"})
            self.assertEqual(json.loads(out.read_text()), {"new": "result"})

    def test_cli_writes_only_after_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/"converted.json"
            cmd=[sys.executable,str(ROOT/"capture_contract.py"),
                 str(ROOT/"fixtures/synthetic-session.json"),"--out",str(out)]
            result=subprocess.run(cmd,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            converted=json.loads(out.read_text())
            self.assertEqual(len(converted["source_manifest_sha256"]),64)
            invalid=Path(temp)/"bad.json";invalid.write_text('{"format": 5}')
            badout=Path(temp)/"not-created.json"
            result=subprocess.run([sys.executable,str(ROOT/"capture_contract.py"),
                                   str(invalid),"--out",str(badout)],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertFalse(badout.exists())
            original=invalid.read_bytes()
            result=subprocess.run([sys.executable,str(ROOT/"capture_contract.py"),
                                   str(invalid),"--out",str(invalid)],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertEqual(invalid.read_bytes(),original)

if __name__ == "__main__":
    unittest.main()
