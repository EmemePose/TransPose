from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import os.path as osp
import sys
import os
import time
import torch

sys.path.insert(0, os.path.dirname(__file__))
from visualize import update_config, add_path
lib_path = osp.join(os.path.dirname(__file__), 'lib')
add_path(lib_path)


import dataset as dataset
from config import cfg
import models
import os
import torchvision.transforms as transforms
import torchvision

import numpy as np 
from lib.core.inference import get_final_preds
from lib.yolo.human_detector import load_model as yolo_model
from lib.yolo.human_detector import main as yolo_det
from lib.utils import get_affine_transform, vis
import cv2

COCO_KEYPOINT_INDEXES = {
    0: 'nose',
    1: 'left_eye',
    2: 'right_eye',
    3: 'left_ear',
    4: 'right_ear',
    5: 'left_shoulder',
    6: 'right_shoulder',
    7: 'left_elbow',
    8: 'right_elbow',
    9: 'left_wrist',
    10: 'right_wrist',
    11: 'left_hip',
    12: 'right_hip',
    13: 'left_knee',
    14: 'right_knee',
    15: 'left_ankle',
    16: 'right_ankle'
}

COCO_INSTANCE_CATEGORY_NAMES = [
    '__background__', 'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus',
    'train', 'truck', 'boat', 'traffic light', 'fire hydrant', 'N/A', 'stop sign',
    'parking meter', 'bench', 'bird', 'cat', 'dog', 'horse', 'sheep', 'cow',
    'elephant', 'bear', 'zebra', 'giraffe', 'N/A', 'backpack', 'umbrella', 'N/A', 'N/A',
    'handbag', 'tie', 'suitcase', 'frisbee', 'skis', 'snowboard', 'sports ball',
    'kite', 'baseball bat', 'baseball glove', 'skateboard', 'surfboard', 'tennis racket',
    'bottle', 'N/A', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl',
    'banana', 'apple', 'sandwich', 'orange', 'broccoli', 'carrot', 'hot dog', 'pizza',
    'donut', 'cake', 'chair', 'couch', 'potted plant', 'bed', 'N/A', 'dining table',
    'N/A', 'N/A', 'toilet', 'N/A', 'tv', 'laptop', 'mouse', 'remote', 'keyboard', 'cell phone',
    'microwave', 'oven', 'toaster', 'sink', 'refrigerator', 'N/A', 'book',
    'clock', 'vase', 'scissors', 'teddy bear', 'hair drier', 'toothbrush'
]

SKELETON = [
    [1,3],[1,0],[2,4],[2,0],[0,5],[0,6],[5,7],[7,9],[6,8],[8,10],[5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16]
]

CocoColors = [[255, 0, 0], [255, 85, 0], [255, 170, 0], [255, 255, 0], [170, 255, 0], [85, 255, 0], [0, 255, 0],
              [0, 255, 85], [0, 255, 170], [0, 255, 255], [0, 170, 255], [0, 85, 255], [0, 0, 255], [85, 0, 255],
              [170, 0, 255], [255, 0, 255], [255, 0, 170], [255, 0, 85]]

NUM_KPTS = 17


def draw_pose(keypoints,img):
    """draw the keypoints and the skeletons.
    :params keypoints: the shape should be equal to [17,2]
    :params img:
    """
    assert keypoints.shape == (NUM_KPTS,2)
    for i in range(len(SKELETON)):
        kpt_a, kpt_b = SKELETON[i][0], SKELETON[i][1]
        x_a, y_a = keypoints[kpt_a][0],keypoints[kpt_a][1]
        x_b, y_b = keypoints[kpt_b][0],keypoints[kpt_b][1] 
        cv2.circle(img, (int(x_a), int(y_a)), 6, CocoColors[i], -1)
        cv2.circle(img, (int(x_b), int(y_b)), 6, CocoColors[i], -1)
        cv2.line(img, (int(x_a), int(y_a)), (int(x_b), int(y_b)), CocoColors[i], 2)

def draw_bbox(box,img):
    """draw the detected bounding box on the image.
    :param img:
    """
    cv2.rectangle(img, box[0], box[1], color=(0, 255, 0),thickness=3)


def get_person_detection_boxes(model, img, threshold=0.5):

    pred = model(img)
    pred_classes = [COCO_INSTANCE_CATEGORY_NAMES[i]
                    for i in list(pred[0]['labels'].cpu().numpy())]  # Get the Prediction Score
    pred_boxes = [[(i[0], i[1]), (i[2], i[3])]
                  for i in list(pred[0]['boxes'].detach().cpu().numpy())]  # Bounding boxes
    pred_score = list(pred[0]['scores'].detach().cpu().numpy())
    if not pred_score or max(pred_score)<threshold:
        return []
    # Get list of index with score greater than threshold
    pred_t = [pred_score.index(x) for x in pred_score if x > threshold][-1]
    pred_boxes = pred_boxes[:pred_t+1]
    pred_classes = pred_classes[:pred_t+1]

    person_boxes = []
    for idx, box in enumerate(pred_boxes):
        if pred_classes[idx] == 'person':
            person_boxes.append(box)
    return person_boxes

def get_pose_estimation_prediction(pose_model, image, center, scale):
    rotation = 0
    # pose estimation transformation
    trans = get_affine_transform(center, scale, rotation, cfg.MODEL.IMAGE_SIZE)
    model_input = cv2.warpAffine(
        image,
        trans,
        (int(cfg.MODEL.IMAGE_SIZE[0]), int(cfg.MODEL.IMAGE_SIZE[1])),
        flags=cv2.INTER_LINEAR)
    transform = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406],
                             std=[0.229, 0.224, 0.225]),
    ])

    # pose estimation inference
    model_input = transform(model_input).unsqueeze(0).to(device)

    # print('input size: ', model_input.shape)

    # switch to evaluate mode
    pose_model.eval()
    with torch.no_grad():
        # compute output heatmap

        output = pose_model(model_input)
        preds, _ = get_final_preds(
            cfg,
            output.clone().cpu().numpy(),
            np.asarray([center]),
            np.asarray([scale]))

        return preds

def box_to_center_scale(box, model_image_width, model_image_height):
    """convert a box to center,scale information required for pose transformation
    Parameters
    ----------
    box : list of tuple
        list of length 2 with two tuples of floats representing
        bottom left and top right corner of a box
    model_image_width : int
    model_image_height : int

    Returns
    -------
    (numpy array, numpy array)
        Two numpy arrays, coordinates for the center of the box and the scale of the box
    """
    center = np.zeros((2), dtype=np.float32)

    bottom_left_corner = box[0]
    top_right_corner = box[1]
    box_width = top_right_corner[0]-bottom_left_corner[0]
    box_height = top_right_corner[1]-bottom_left_corner[1]
    bottom_left_x = bottom_left_corner[0]
    bottom_left_y = bottom_left_corner[1]
    center[0] = bottom_left_x + box_width * 0.5
    center[1] = bottom_left_y + box_height * 0.5

    aspect_ratio = model_image_width * 1.0 / model_image_height
    pixel_std = 200

    if box_width > aspect_ratio * box_height:
        box_height = box_width * 1.0 / aspect_ratio
    elif box_width < aspect_ratio * box_height:
        box_width = box_height * aspect_ratio
    scale = np.array(
        [box_width * 1.0 / pixel_std, box_height * 1.0 / pixel_std],
        dtype=np.float32)
    if center[0] != -1:
        scale = scale * 1.25

    return center, scale



def set_yolo_model():
     ### detection model
    box_model = yolo_model()
    return box_model


def set_pose_model(model_name, yaml_path):
    file_name = yaml_path # 'experiments/coco/transpose_r/TP_R_256x192_d256_h1024_enc4_mh8.yaml' # choose a yaml file
    f = open(file_name, 'r')
    update_config(cfg, file_name)
    ### 2d pose estimation model
    model_name = model_name # 'T-H-A4'
    assert model_name in ['T-R', 'T-H','T-H-L','T-R-A4', 'T-H-A6', 'T-H-A5', 'T-H-A4' ,'T-R-A4-DirectAttention']
    pose_model = eval( 'models.'+cfg.MODEL.NAME+'.get_pose_net')(
        cfg, is_train=True
    )
    if cfg.TEST.MODEL_FILE:
        print('=> loading pose_model from {}'.format(cfg.TEST.MODEL_FILE))
        pose_model.load_state_dict(torch.load( osp.join(osp.dirname(__file__), cfg.TEST.MODEL_FILE) ,map_location=device ), strict=True)
    else:
        raise ValueError("please choose one ckpt in cfg.TEST.MODEL_FILE")
    return pose_model



os.environ['CUDA_VISIBLE_DEVICES'] ='0'
device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')



def estimate_2d(video_path, device):


    box_model = set_yolo_model()
    box_model.to(device)
    box_model.eval()
    yaml_path = osp.join( osp.dirname(__file__) , 'experiments/coco/transpose_r/TP_R_256x192_d256_h1024_enc4_mh8.yaml')
    pose_model = set_pose_model(model_name='T-H-A4', yaml_path=yaml_path)
    pose_model.to(device)
    pose_model.eval()

    joint_2d_frames = []
    vidcap = cv2.VideoCapture(video_path)
    while True:
        ret, image_bgr = vidcap.read()
        if ret:
            ## human detection
            last_time = time.time()
            pred_boxes, scores = yolo_det(image_bgr, box_model )
            pred_boxes = [  [(box[0], box[1]), (box[2], box[3])]   for box in pred_boxes]
            image = image_bgr[:, :, [2, 1, 0]]

            ## pose estimation
            if len(pred_boxes) >= 1:
                for box in pred_boxes:
                    center, scale = box_to_center_scale(box, cfg.MODEL.IMAGE_SIZE[0], cfg.MODEL.IMAGE_SIZE[1])
                    image_pose = image.copy() if cfg.DATASET.COLOR_RGB else image_bgr.copy()
                    pose_preds = get_pose_estimation_prediction(pose_model, image_pose, center, scale)                    
                    joint_2d_frames.append(pose_preds)
                    ## pose_preds: list with shape N, 17, 2 
                    if len(pose_preds)>=1:
                        for kpt in pose_preds:
                            draw_pose(kpt,image_bgr) # draw the poses
             
            fps = 1/(time.time()-last_time)
            print('FPS: ', fps)
        else:
            print('cannot load the video.')
            break
    vidcap.release()
    return np.array(joint_2d_frames)

def inference2d(video_path):
    vidcap = cv2.VideoCapture(video_path)
    
    ### detection model
    box_model = yolo_model()
    box_model.to(device)
    box_model.eval()


    
    file_name = 'experiments/coco/transpose_r/TP_R_256x192_d256_h1024_enc4_mh8.yaml' # choose a yaml file
    f = open(file_name, 'r')
    update_config(cfg, file_name)
    ### 2d pose estimation model
    model_name = 'T-H-A4'
    assert model_name in ['T-R', 'T-H','T-H-L','T-R-A4', 'T-H-A6', 'T-H-A5', 'T-H-A4' ,'T-R-A4-DirectAttention']
    pose_model = eval('models.'+cfg.MODEL.NAME+'.get_pose_net')(
        cfg, is_train=True
    )
    if cfg.TEST.MODEL_FILE:
        print('=> loading pose_model from {}'.format(cfg.TEST.MODEL_FILE))
        pose_model.load_state_dict(torch.load(osp.join(osp.dirname(__file__), cfg.TEST.MODEL_FILE)), strict=True)
    else:
        raise ValueError("please choose one ckpt in cfg.TEST.MODEL_FILE")

    
    ########################################################################
    # print("pose_model params:{:.3f}M".format(sum([p.numel() for p in pose_model.parameters()])/1000**2))
    pose_model.to(device)
    pose_model.eval()
    joint_2d_frames = []
    while True:
        ret, image_bgr = vidcap.read()
        # print('image_size, ', image_bgr.shape)
        # exit(0)
        if ret:
            last_time = time.time()
            detect_time = time.time()
            pred_boxes, scores = yolo_det(image_bgr, box_model)
            pred_boxes = [  [(box[0], box[1]), (box[2], box[3])]   for box in pred_boxes]
            # print('detection takes: ', time.time() - detect_time)

            image = image_bgr[:, :, [2, 1, 0]]

            # pose estimation
            if len(pred_boxes) >= 1:
                for box in pred_boxes:
                    center, scale = box_to_center_scale(box, cfg.MODEL.IMAGE_SIZE[0], cfg.MODEL.IMAGE_SIZE[1])
                    image_pose = image.copy() if cfg.DATASET.COLOR_RGB else image_bgr.copy()
                    pose_preds = get_pose_estimation_prediction(pose_model, image_pose, center, scale)

                    print('1 frame takes: ', time.time() - detect_time)
                    
                    joint_2d_frames.append(pose_preds)
                    ## pose_preds: list with shape N, 17, 2 
                    if len(pose_preds)>=1:
                        for kpt in pose_preds:
                            draw_pose(kpt,image_bgr) # draw the poses
             
            fps = 1/(time.time()-last_time)
            print('FPS: ', fps)

        else:
            print('cannot load the video.')
            break


    vidcap.release()
    return np.array(joint_2d_frames)

def real_time():
    args = parse_args()
    os.environ['CUDA_VISIBLE_DEVICES'] ='0'
    file_name = 'experiments/coco/transpose_r/TP_R_256x192_d256_h1024_enc4_mh8.yaml' # choose a yaml file
    f = open(file_name, 'r')
    update_config(cfg, file_name)

    ### detection model
    yolo_box_model = yolo_model()
    box_model = torchvision.models.detection.fasterrcnn_resnet50_fpn(pretrained=True)
    box_model.to(device)
    box_model.eval()
    ### 2d pose estimation model
    model_name = 'T-H-A4'
    assert model_name in ['T-R', 'T-H','T-H-L','T-R-A4', 'T-H-A6', 'T-H-A5', 'T-H-A4' ,'T-R-A4-DirectAttention']
    pose_model = eval('models.'+cfg.MODEL.NAME+'.get_pose_net')(
        cfg, is_train=True
    )
    if cfg.TEST.MODEL_FILE:
        print('=> loading pose_model from {}'.format(cfg.TEST.MODEL_FILE))
        pose_model.load_state_dict(torch.load(    osp.join(osp.dirname(__file__), cfg.TEST.MODEL_FILE))  , strict=True)
    else:
        raise ValueError("please choose one ckpt in cfg.TEST.MODEL_FILE")

    pose_model.to(device)
    print("pose_model params:{:.3f}M".format(sum([p.numel() for p in pose_model.parameters()])/1000**2))

    ########################################################################
    # pose_model = torch.nn.DataParallel(pose_model, device_ids=cfg.GPUS)
    pose_model.to(device)
    pose_model.eval()
    
    # Loading an video or an image or webcam 
    if args.webcam:
        vidcap = cv2.VideoCapture(0)
    elif args.video:
        vidcap = cv2.VideoCapture(args.video)
    elif args.image:
        image_bgr = cv2.imread(args.image)
    else:
        print('please use --video or --webcam or --image to define the input.')
        return 

    if args.webcam or args.video:
        if args.write:
            save_path = 'output.avi'
            fourcc = cv2.VideoWriter_fourcc(*'XVID')
            out = cv2.VideoWriter(save_path,fourcc, 24.0, (int(vidcap.get(3)),int(vidcap.get(4))))
        while True:
            ret, image_bgr = vidcap.read()
            if ret:
                last_time = time.time()
                detect_time = time.time()

                pred_boxes, scores = yolo_det(image_bgr, yolo_box_model)
                pred_boxes = [  [(box[0], box[1]), (box[2], box[3])]   for box in pred_boxes]

                # print('detection takes: ', time.time() - detect_time)


                ## object detection box using Faster R-CNN (slow)
                image = image_bgr[:, :, [2, 1, 0]]

                # input = []
                # img = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
                # # img = cv2.resize(img, (0,0), fx=0.5, fy=0.5)
                # img_tensor = torch.from_numpy(img/255.).permute(2,0,1).float().to(device)
                # input.append(img_tensor)
                # input = torch.stack(input)
                # pred_boxes = get_person_detection_boxes(box_model, input, threshold=0.9)

                ##
                # pose estimation
                if len(pred_boxes) >= 1:
                    for box in pred_boxes:
                        center, scale = box_to_center_scale(box, cfg.MODEL.IMAGE_SIZE[0], cfg.MODEL.IMAGE_SIZE[1])
                        image_pose = image.copy() if cfg.DATASET.COLOR_RGB else image_bgr.copy()
                        pose_time = time.time()
                        pose_preds = get_pose_estimation_prediction(pose_model, image_pose, center, scale)
                        print('pose takes: ', time.time() - pose_time)
                        ## pose_preds: list with shape N, 17, 2 
                        if len(pose_preds)>=1:
                            for kpt in pose_preds:
                                draw_pose(kpt, image_bgr) # draw the poses

                if args.showFps:
                    fps = 1/(time.time()-last_time)
                    img = cv2.putText(image_bgr, 'fps: '+ "%.2f"%(fps), (25, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 2)

                if args.write:
                    out.write(image_bgr)

                cv2.imshow('demo',image_bgr)
                if cv2.waitKey(1) & 0XFF==ord('q'):
                    break
            else:
                print('cannot load the video.')
                break


        cv2.destroyAllWindows()
        vidcap.release()
        if args.write:
            print('video has been saved as {}'.format(save_path))
            out.release()


def parse_args():
    parser = argparse.ArgumentParser(description='Train keypoints network')
    # general
    # parser.add_argument('--cfg', type=str, default='experiments/coco/transpose_r/TP_R_256x192_d256_h1024_enc4_mh8.yaml')
    parser.add_argument('--video', type=str)
    parser.add_argument('--webcam',action='store_true')
    parser.add_argument('--image',type=str)
    parser.add_argument('--write',action='store_true')
    parser.add_argument('--showFps',action='store_true', default=True)

    parser.add_argument('opts',
                        help='Modify config options using the command-line',
                        default=None,
                        nargs=argparse.REMAINDER)

    args = parser.parse_args()
    return args
if __name__ == '__main__':
    args = parse_args()
    s_time = time.time()
    joints_2d = inference2d(args.video)
    print('infer 2d pose takes: ', time.time() - s_time)
    print(joints_2d)
    
    np.save(osp.basename(args.video).split('.')[0] + '.npy', joints_2d)
    
    
    # joints_2d = np.load('v6.npy')
    # print(joints_2d.shape)
    