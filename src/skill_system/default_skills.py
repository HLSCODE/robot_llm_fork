"""
默认技能定义
当 skill_library.json 不存在时，使用这里定义的技能
"""
from typing import List
from .models import Skill, SkillCategory, SkillParameter, SkillStep


def get_default_skills() -> List[Skill]:
    """
    获取默认技能列表
    这些技能在没有 skill_library.json 文件时使用
    """
    return [
        # 抓取类
        Skill(
            id="grab_bottle",
            name="抓取瓶子",
            category=SkillCategory.GRAB,
            description="从瓶子上方下降，夹爪抓取瓶子",
            icon="🫙",
            parameters=[],
            steps=[
                SkillStep(
                    step_id="1",
                    action_name="pingzishang",
                    action_type="MOVE",
                    parameters={"臂": "左", "模式": "move_l", "点位": "[0.068791,-0.011241,-0.423676,-3.107000,0.000000,1.603000]"},
                    description="移动到瓶子上方",
                    estimated_time=3.0
                ),
                SkillStep(
                    step_id="2",
                    action_name="jiazhua kai",
                    action_type="MANIPULATE",
                    parameters={"执行器": "夹爪", "编号": 1, "操作": "开"},
                    description="打开夹爪",
                    estimated_time=1.0
                ),
                SkillStep(
                    step_id="3",
                    action_name="pingzijia",
                    action_type="MOVE",
                    parameters={"臂": "左", "模式": "move_l", "点位": "[0.068791,-0.011249,-0.488797,-3.107000,0.000000,1.602000]"},
                    description="下降到瓶子位置",
                    estimated_time=2.0
                ),
                SkillStep(
                    step_id="4",
                    action_name="jiazhua guan",
                    action_type="MANIPULATE",
                    parameters={"执行器": "夹爪", "编号": 1, "操作": "关"},
                    description="关闭夹爪抓取瓶子",
                    estimated_time=1.0
                ),
            ],
            examples=["帮我抓一个瓶子", "抓取瓶子", "夹爪抓取"],
            tags=["抓取", "瓶子", "夹爪", "grab"]
        ),

        Skill(
            id="release_bottle",
            name="释放瓶子",
            category=SkillCategory.GRAB,
            description="打开夹爪，释放瓶子",
            icon="🫙",
            parameters=[],
            steps=[
                SkillStep(
                    step_id="1",
                    action_name="jiazhua kai",
                    action_type="MANIPULATE",
                    parameters={"执行器": "夹爪", "编号": 1, "操作": "开"},
                    description="打开夹爪释放瓶子",
                    estimated_time=1.0
                ),
            ],
            examples=["放开瓶子", "释放瓶子", "打开夹爪"],
            tags=["释放", "瓶子", "夹爪", "open"]
        ),

        # 吸液类
        Skill(
            id="absorb_liquid",
            name="吸取液体",
            category=SkillCategory.INSPECT,
            description="使用吸液枪吸取指定容量的液体",
            icon="💉",
            parameters=[
                SkillParameter(
                    name="volume",
                    param_label="容量",
                    type="int",
                    description="吸取容量(ul)",
                    default=500,
                    required=False
                )
            ],
            steps=[
                SkillStep(
                    step_id="1",
                    action_name="qu1",
                    action_type="CHANGE_GUN",
                    parameters={"Gun_Position": 1, "Operation": "取"},
                    description="取吸液枪",
                    estimated_time=3.0
                ),
                SkillStep(
                    step_id="2",
                    action_name="xiye",
                    action_type="MANIPULATE",
                    parameters={"执行器": "吸液枪", "操作": "吸", "容量": 500},
                    description="吸取液体",
                    estimated_time=2.0
                ),
            ],
            examples=["吸取500微升液体", "吸液500ul", "帮我抽一些液体"],
            tags=["吸液", "液体", "吸取", "absorb"]
        ),

        Skill(
            id="dispense_liquid",
            name="释放液体",
            category=SkillCategory.INSPECT,
            description="使用吸液枪释放已吸取的液体",
            icon="💉",
            parameters=[],
            steps=[
                SkillStep(
                    step_id="1",
                    action_name="tuye",
                    action_type="MANIPULATE",
                    parameters={"执行器": "吸液枪", "操作": "吐", "容量": 500},
                    description="释放液体",
                    estimated_time=2.0
                ),
            ],
            examples=["释放液体", "吐液", "排出液体"],
            tags=["吐液", "液体", "释放", "dispense"]
        ),

        # 检测类
        Skill(
            id="inspect_sensor",
            name="传感器检测",
            category=SkillCategory.INSPECT,
            description="执行传感器检测，判断是否通过阈值",
            icon="🔍",
            parameters=[
                SkillParameter(name="sensor_id", param_label="传感器ID", type="int", description="传感器编号", default=2, required=False),
                SkillParameter(name="threshold", param_label="阈值", type="float", description="检测阈值", default=0.0, required=False),
            ],
            steps=[
                SkillStep(
                    step_id="1",
                    action_name="检测",
                    action_type="INSPECT",
                    parameters={"Sensor_ID": "2", "Threshold": 0.0, "Timeout": 5.0},
                    description="执行传感器检测",
                    estimated_time=5.0
                ),
            ],
            examples=["检测一下", "执行传感器检测", "检测传感器"],
            tags=["检测", "传感器", "inspect"]
        ),

        # 移动类
        Skill(
            id="move_to_home",
            name="回到安全位置",
            category=SkillCategory.MOVE,
            description="机械臂移动到安全起始位置",
            icon="🏠",
            parameters=[],
            steps=[
                SkillStep(
                    step_id="1",
                    action_name="hengzhejia",
                    action_type="MOVE",
                    parameters={"臂": "左", "模式": "move_j", "点位": "pos_hengzhejia = [0.277553,-0.002143,-0.436172,2.441000,1.486000,-0.628000]"},
                    description="移动到横折架位置",
                    estimated_time=3.0
                ),
            ],
            examples=["回到安全位置", "移动到起始位置", "归位"],
            tags=["移动", "归位", "home", "安全位置"]
        ),

        # 工具类
        Skill(
            id="pick_tool_1",
            name="更换工具1",
            category=SkillCategory.TOOL,
            description="从枪位1取下工具并安装到快换手上",
            icon="🔧",
            parameters=[],
            steps=[
                SkillStep(
                    step_id="1",
                    action_name="qu1",
                    action_type="CHANGE_GUN",
                    parameters={"Gun_Position": 1, "Operation": "取"},
                    description="从枪位1取工具",
                    estimated_time=3.0
                ),
            ],
            examples=["取工具1", "更换到工具1", "装上工具1"],
            tags=["工具", "更换", "取", "tool"]
        ),

        Skill(
            id="place_tool_1",
            name="放回工具1",
            category=SkillCategory.TOOL,
            description="将快换手上的工具放回枪位1",
            icon="🔧",
            parameters=[],
            steps=[
                SkillStep(
                    step_id="1",
                    action_name="fang1",
                    action_type="CHANGE_GUN",
                    parameters={"Gun_Position": 1, "Operation": "放"},
                    description="将工具放回枪位1",
                    estimated_time=3.0
                ),
            ],
            examples=["放回工具1", "把工具放回去", "退枪"],
            tags=["工具", "放回", "退枪", "tool"]
        ),
    ]
