import time
import cv2
import cv2.aruco as aruco

aruco_dict = aruco.Dictionary_get(aruco.DICT_4X4_250)
parameters = aruco.DetectorParameters_create()

def in_id_fn(parent):
    print('=== id')

    for i, img in enumerate(parent.cur_imgs):
        _, ids, _ = aruco.detectMarkers(gray, aruco_dict, parameters=parameters)

        if ids and ids[0][0] == 42:
            print("Found drone")
            parent.drone_bbox = parent.cur_bbox[i]
            parent.drone()
        else:
            print("Not a drone")
    
    print("No drone :(")
    parent.not_drone()

def out_id_fn(parent):
    pass
