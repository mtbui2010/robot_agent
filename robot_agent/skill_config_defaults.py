"""
Default values for skill configs, resolved from the legacy
carerobotapp runtime (tasks.py → robot_10.py → robot_comon.py +
word_mapping.py). These dicts seed the _ConfigProxy fallbacks in
skill_configs.py and are what skills read when no override is set.

Edit-then-Save from the UI persists changes to skill_configs_override.json.
"""

GRIP_CONFIGS_DEFAULT = {
    'range': [0, 0.1],
}

LIFT_CONFIGS_DEFAULT = {
    'range': [0.11, 0.67],
    'home': {'front': 0.4, 'left': 0.4, 'right': 0.4},
}

HEAD_CONFIGS_DEFAULT = {
    'ry_range': [-50, 10],
    'rz': {'front': 0, 'left': -70, 'right': 70},
    'ry': {'up': 10, 'straight': -15, 'down': -50},
}

ARM_CONFIGS_DEFAULT = {
    'calib_offset': [0, 0, -30],
    'wrist_cam_offset': [-20, -75, 0],
    'base_x': 191.5,
    'range': {
        'x': [441.5, 491.5],
        'y': [-1000, 1000],
        'z': [-100, 1500],
    },
    'tool_range': {
        'x': [-500, 500],
        'y': [-500, 500],
        'z': [200, 900],
    },
    'wrist_angle': {
        'lying': 45,
        'standing': 10,
    },
    'approach_d': 120,
    # Joint pose values are in DEGREES. `movej` converts to radians before send().
    'home': {
        'front': [0,   15, 0, 15, -180, 0, 0],
        'left':  [-90, 15, 0, 15, -180, 0, 0],
        'right': [90,  15, 0, 15, -180, 0, 0],
    },
    'ready': {
        'front': [0,   15, 0, 15, -180, 90, 0],
        'left':  [-90, 15, 0, 15, -180, 90, 0],
        'right': [90,  15, 0, 15, -180, 90, 0],
    },
    'fold': {
        'front': [90,  0,   20,    12,  -40,   97,   -10],
        'left':  [70,  -14, -22.9, 9.4, -49.9, 95.2, -17],
        'right': [-70, 0,   23.3,  9.7, -308,  80,   6.6],
    },
    'unfold': {
        'front': [0,     -26.3, 18.8,  1.5,  -160.5, 62.2, 0],
        'right': [-11.8, -19.1, -25.7, 12.3, -324.7, 60.7, 32],
        'left':  [3.7,   -20.6, 26.8,  8.3,  -36.7,  56.2, -34.8],
    },
    'give': {
        'left':  [-17.7, -10.5, 23.7,  7.3, -77,    73.7, -16.8],
        'right': [13.9,  -8.2,  -20.4, 8,   -279.3, 74.3, 18],
    },
    'approach_lying': {
        'right': [89.6, -26.8, 0.3,    28.4, 0.9,  55,   -179.9],
        'left':  [86,   23.3,  -182.5, 37,   -0.2, 60.4, -185.5],
    },
    'j_arm_speed': 1.5,
    'j_arm_accel': 12.0,
    'l_arm_speed': 400.0,
    'l_arm_accel': 1200.0,
    'tool_radius': 50.0,
    'tool_length': 260,
    'wrist_tool_length': 216,
    'approach_wrist_angle': 45,
    'place_liftup': 130,
}

MOBILE_CONFIGS_DEFAULT = {
    'dforward': 200,
    'dshift': -441.5,
    'height': 405,
    'max_shift': 600,
}

FIND_CONFIGS_DEFAULT = {
    'detector': 'groundingdino',
    'camera': 'head',
    'head_angles': [-25],
    'params': {
        'min_mass': 200,
        'max_ratio': 0.6,
        'box_threshold': 0.28,
        'text_threshold': 0.28,
    },
    'standing_obj_names': ['cup', 'bottle'],
    'lying_obj_names': ['phone', 'fork', ' spoon', 'bread', 'banana', 'control'],
    'having_handle_obj_names': [],
    'target_distance': 650,
}

CALIB_PARAMS_DEFAULT = {
    'link2base_robot': [
        [1.0, 0.0, 0.0, -105.5],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 930.99],
        [0.0, 0.0, 0.0, 1.0],
    ],
    'base2LMbase_robot': [
        [1.0, 0.0, 0.0, 191.5],
        [0.0, 1.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 115.0],
        [0.0, 0.0, 0.0, 1.0],
    ],
    'error_linear': [
        [-0.07502618754327467, 2.4424700738230953],
        [-16.842424532247115, -446.11492970318335],
        [9.553416611923009, 183.18383747945558],
    ],
    'error_linear_front': [
        [-0.07502618754327467, 2.4424700738230953],
        [-16.842424532247115, -446.11492970318335],
        [9.553416611923009, 183.18383747945558],
    ],
    'error_linear_left': [
        [9.210932763507733, 184.6078786695192],
        [-14.437338038556312, -378.47661712845184],
        [0.28343902543810057, 2.4396699456603343],
    ],
    'error_linear_right': [
        [-9.84392375612643, -200.58470322524005],
        [-14.726481118427895, -383.38040346872526],
        [-0.047364594742677804, 1.3281026351637906],
    ],
    'T_1': [32, -75.13, 60.9],
    'T_2': [0, 0, 28],
    'tune_xyz': [0, 50, 0],
    'init_angle': True,
    'fx': 746.1448364257812,
    'ppx': 629.9429931640625,
    'fy': 746.0178833007812,
    'ppy': 356.381591796875,
    'lm_max_value': 935,
}

HOME_LOC_DEFAULT = {
    'x': 200,
    'y': 0,
    'theta': 0,
}

ENV_DEFAULT = {
    'table@living room': {
        'default_mode': 'left',
        'placepose': {
            'dx': 0,
        },
        'height': 315,
        'loc': {
            'x': 1000,
            'y': -1500,
            'theta': 0,
        },
        'dforward': 100,
    }
}

KR2EN_DEFAULT = {
    '콜라': 'coke',
    '사이다': 'green drink',
    '커피': 'coffee',
    '거실': 'living room',
    '테이블': 'table',
    '침대 옆 서랍장': 'bed table',
    '쇼파 옆 테이블': 'sopha table',
    '안방': 'bedroom',
    '수건': 'towel',
    '닦는수건': 'cleaning towel',
    '화장실': 'restroom',
    '주방': 'kitchen',
    '휴지': 'blue tissue',
    '치약': 'toothpaste',
    '식탁': 'dining table',
    '컵': 'cup',
    '쏟은 음료': 'spill',
    '리모컨': 'controller',
    '캔': 'can',
    '선반': 'shelf',
    '불': 'light',
    '폰': 'phone',
    '분리수거함': 'can trash',
    '싱크볼': 'sink',
    '서랍장': 'drawer',
    '펜': 'pen',
    '물병': 'water bottle',
    '우유': 'green bag',
}

EN2KR_DEFAULT = {
    'coke': '콜라',
    'green drink': '사이다',
    'coffee': '커피',
    'living room': '거실',
    'table': '테이블',
    'bed table': '침대 옆 서랍장',
    'sopha table': '쇼파 옆 테이블',
    'bedroom': '안방',
    'towel': '수건',
    'cleaning towel': '닦는수건',
    'restroom': '화장실',
    'kitchen': '주방',
    'blue tissue': '휴지',
    'toothpaste': '치약',
    'dining table': '식탁',
    'cup': '컵',
    'spill': '쏟은 음료',
    'controller': '리모컨',
    'can': '캔',
    'shelf': '선반',
    'light': '불',
    'phone': '폰',
    'can trash': '분리수거함',
    'sink': '싱크볼',
    'drawer': '서랍장',
    'pen': '펜',
    'water bottle': '물병',
    'green bag': '우유',
}
