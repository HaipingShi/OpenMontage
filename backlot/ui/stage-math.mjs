// Pure metre-based stage maths, shared by the browser and node --test.
const positive = value => { if (!Number.isFinite(value) || value <= 0) throw new RangeError('需要正数'); return value; };
const dot = (a, b) => a.reduce((sum, v, i) => sum + v * b[i], 0);
const subtract = (a, b) => a.map((v, i) => v - b[i]);
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const normalise = a => { const n = Math.hypot(...a); positive(n); return a.map(v => v / n || 0); };

export const POSES = ['standing', 'sitting', 'walking', 'kneeling', 'reaching'];
export const JOINT_NAMES = ['shoulder_left', 'elbow_left', 'shoulder_right', 'elbow_right', 'hip_left', 'knee_left', 'hip_right', 'knee_right'];
export const BONES = [['head', 'neck'], ['neck', 'shoulder_left'], ['neck', 'shoulder_right'], ['neck', 'pelvis'],
  ['shoulder_left', 'elbow_left'], ['elbow_left', 'wrist_left'], ['shoulder_right', 'elbow_right'], ['elbow_right', 'wrist_right'],
  ['pelvis', 'hip_left'], ['pelvis', 'hip_right'], ['hip_left', 'knee_left'], ['knee_left', 'ankle_left'], ['hip_right', 'knee_right'], ['knee_right', 'ankle_right']];

export function fovDegrees(lens, sensorWidth, sensorHeight, aspect) {
  [lens, sensorWidth, sensorHeight, aspect].forEach(positive);
  return 2 * Math.atan(Math.min(sensorHeight, sensorWidth / aspect) / (2 * lens)) * 180 / Math.PI;
}

export function resolutionSize(value) {
  const match = /^(\d+)x(\d+)$/.exec(String(value));
  if (!match) throw new RangeError('分辨率格式应为 1920x1080');
  const [width, height] = match.slice(1).map(Number);
  [width, height].forEach(positive);
  if (width > 4096 || height > 4096) throw new RangeError('舞台导出最大边长 4096');
  return {width, height};
}

export function cameraBasis(camera) {
  const forward = normalise(subtract(camera.target, camera.position));
  let right = cross(forward, [0, 1, 0]);
  if (Math.hypot(...right) < 1e-8) right = cross(forward, [0, 0, 1]);
  right = normalise(right);
  return {forward, right, up: normalise(cross(right, forward))};
}

export function projectPoint(point, camera, width, height) {
  positive(width); positive(height);
  const {forward, right, up} = cameraBasis(camera);
  const delta = subtract(point, camera.position);
  const depth = dot(delta, forward);
  const slope = Math.tan(fovDegrees(camera.lens_mm, camera.sensor_width_mm, camera.sensor_height_mm, width / height) * Math.PI / 360);
  const ndcX = depth === 0 ? 0 : dot(delta, right) / (depth * slope * width / height);
  const ndcY = depth === 0 ? 0 : dot(delta, up) / (depth * slope);
  return {x: (ndcX + 1) * width / 2, y: (1 - ndcY) * height / 2, ndcX, ndcY, depth,
    visible: depth >= camera.near && depth <= camera.far && Math.abs(ndcX) <= 1 && Math.abs(ndcY) <= 1};
}

function rotateEuler(point, rotation) {
  let [x, y, z] = point;
  const [rx, ry, rz] = rotation;
  [x, y] = [x * Math.cos(rz) - y * Math.sin(rz), x * Math.sin(rz) + y * Math.cos(rz)];
  [x, z] = [x * Math.cos(ry) + z * Math.sin(ry), -x * Math.sin(ry) + z * Math.cos(ry)];
  [y, z] = [y * Math.cos(rx) - z * Math.sin(rx), y * Math.sin(rx) + z * Math.cos(rx)];
  return [x, y, z];
}

export function defaultStage() {
  return {version: '1.0', units: 'm', objects: [], camera: {position: [0, 1.6, 6], target: [0, 1, 0], lens_mm: 50,
    sensor_width_mm: 36, sensor_height_mm: 24, near: .1, far: 100}};
}

export function createStageObject(kind, subject_ref = '', id = `${kind}-${subject_ref || '1'}`) {
  if (!['human', 'box', 'cylinder', 'plane'].includes(kind)) throw new RangeError('不支持的物体');
  return {id, kind, subject_ref, color: '#d4a768', position: [0, 0, 0], rotation: [0, 0, 0], scale: [1, 1, 1],
    ...(kind === 'human' ? {pose: 'standing', joints: {}} : {})};
}

export function poseWorldJoints(object) {
  object = {...createStageObject('human', object.subject_ref, object.id), ...object};
  const presets = {standing: [0, 0, 0, 0, 0, 0, 0, 0], sitting: [.15, .25, .15, .25, Math.PI / 2, -Math.PI / 2, Math.PI / 2, -Math.PI / 2],
    walking: [-.45, .2, .45, .2, .45, -.2, -.45, -.25], kneeling: [.35, .25, .35, .25, .2, -1.7, .2, -1.7],
    reaching: [2, -.35, 1.3, -.5, 0, 0, 0, 0]};
  const angles = Object.fromEntries(JOINT_NAMES.map((name, i) => [name, presets[object.pose || 'standing'][i]]));
  Object.assign(angles, object.joints || {});
  const hipHeight = object.pose === 'sitting' ? .55 : object.pose === 'kneeling' ? .5 : .95;
  const joints = {pelvis: [0, hipHeight, 0], neck: [0, hipHeight + .55, 0], head: [0, hipHeight + .77, 0]};
  const rotation = angle => Array.isArray(angle) ? angle : [-angle, 0, 0];
  const limb = (origin, length, parentAngle, childAngle = 0) => {
    const direction = rotateEuler(rotateEuler([0, -length, 0], rotation(childAngle)), rotation(parentAngle));
    return origin.map((v, i) => v + direction[i]);
  };
  for (const [side, x] of [['left', -.24], ['right', .24]]) {
    joints[`shoulder_${side}`] = [x, hipHeight + .47, 0];
    joints[`elbow_${side}`] = limb(joints[`shoulder_${side}`], .31, angles[`shoulder_${side}`]);
    joints[`wrist_${side}`] = limb(joints[`elbow_${side}`], .28, angles[`shoulder_${side}`], angles[`elbow_${side}`]);
    joints[`hip_${side}`] = [x / 2, hipHeight, 0];
    joints[`knee_${side}`] = limb(joints[`hip_${side}`], .45, angles[`hip_${side}`]);
    joints[`ankle_${side}`] = limb(joints[`knee_${side}`], .45, angles[`hip_${side}`], angles[`knee_${side}`]);
  }
  return Object.fromEntries(Object.entries(joints).map(([name, point]) => [name, rotateEuler(point.map((v, i) => v * object.scale[i]), object.rotation).map((v, i) => v + object.position[i])]));
}

export function placements(objects, camera, width, height) {
  const items = objects.filter(object => object.subject_ref).map(object => ({object, projection: projectPoint(object.position, camera, width, height)}));
  if (!items.length) return {};
  const depths = items.map(item => item.projection.depth).filter(value => value > 0);
  const low = Math.min(...depths), high = Math.max(...depths);
  return Object.fromEntries(items.map(({object, projection}) => {
    const {ndcX, depth} = projection;
    const horizontal = ndcX < -1 / 3 ? '左侧' : ndcX > 1 / 3 ? '右侧' : '中央';
    const fraction = high === low ? .5 : (depth - low) / (high - low);
    const layer = fraction < 1 / 3 ? '前景' : fraction > 2 / 3 ? '后景' : '中景';
    const direction = normalise(subtract(camera.position, object.position));
    const facing = dot(rotateEuler([0, 0, 1], object.rotation || [0, 0, 0]), direction);
    return [object.subject_ref, `${depth <= 0 ? '镜头后方' : `画面${horizontal}，${layer}`}，${facing > .5 ? '朝向镜头' : facing < -.5 ? '背向镜头' : '侧身'}`];
  }));
}

export function poseKeypoints(stage, width, height) {
  return {version: '1.0', format: 'openmontage-joints-v1', width, height,
    people: stage.objects.filter(object => object.kind === 'human').map(object => ({subject_ref: object.subject_ref,
      object_id: object.id, keypoints: Object.entries(poseWorldJoints(object)).map(([name, point]) => {
        const projection = projectPoint(point, stage.camera, width, height);
        return {name, x: projection.x, y: projection.y, visible: projection.visible, confidence: projection.visible ? 1 : 0};
      })}))};
}

export function normalisedDepth(distance, near, far) {
  positive(near); positive(far);
  if (far <= near || !Number.isFinite(distance)) throw new RangeError('无效深度范围');
  return Math.max(0, Math.min(1, 1 - (distance - near) / (far - near)));
}
