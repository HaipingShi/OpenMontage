import test from 'node:test';
import assert from 'node:assert/strict';
import { fovDegrees, cameraBasis, projectPoint, placements, poseWorldJoints,
  poseKeypoints, normalisedDepth, defaultStage, createStageObject, resolutionSize }
  from '../../backlot/ui/stage-math.mjs';

const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-8, `${actual} != ${expected}`);
const camera = {position: [0, 1, 6], target: [0, 1, 0], lens_mm: 50,
  sensor_width_mm: 36, sensor_height_mm: 24, near: .1, far: 100};

test('36×24 sensor is cropped for both landscape and portrait, with a real focal length', () => {
  close(fovDegrees(50, 36, 24, 16 / 9), 2 * Math.atan(20.25 / 100) * 180 / Math.PI);
  close(fovDegrees(50, 36, 24, 9 / 16), 2 * Math.atan(24 / 100) * 180 / Math.PI);
  assert.ok(fovDegrees(24, 36, 24, 16 / 9) > fovDegrees(85, 36, 24, 16 / 9));
  for (const invalid of [0, -1, NaN, Infinity]) assert.throws(() => fovDegrees(invalid, 36, 24, 1));
  assert.throws(() => fovDegrees(50, 0, 24, 1));
  assert.throws(() => fovDegrees(50, 36, 0, 1));
  assert.throws(() => fovDegrees(50, 36, 24, 0));
});

test('camera basis handles rotated and vertically pointing cameras', () => {
  assert.deepEqual(cameraBasis(camera).right, [1, 0, 0]);
  const turned = cameraBasis({...camera, position: [6, 1, 0]});
  close(turned.right[2], -1);
  const vertical = cameraBasis({...camera, position: [0, 6, 0], target: [0, 0, 0]});
  close(Math.hypot(...vertical.right), 1);
  assert.throws(() => cameraBasis({...camera, target: camera.position}));
});

test('projection is camera-relative, centered, and rejects behind-camera and clipped joints', () => {
  const middle = projectPoint([0, 1, 0], camera, 1920, 1080);
  close(middle.x, 960); close(middle.y, 540); assert.equal(middle.visible, true);
  assert.ok(projectPoint([-1, 1, 0], camera, 1920, 1080).x < 960);
  assert.equal(projectPoint([0, 1, 7], camera, 1920, 1080).visible, false);
  assert.equal(projectPoint([0, 1, 5.95], camera, 1920, 1080).visible, false);
  assert.equal(projectPoint([0, 1, -200], camera, 1920, 1080).visible, false);
  assert.equal(projectPoint([50, 1, 0], camera, 1920, 1080).visible, false);
  const rotated = {...camera, position: [6, 1, 0], target: [0, 1, 0]};
  assert.ok(projectPoint([0, 1, 1], rotated, 1920, 1080).x < 960);
  assert.throws(() => projectPoint([0, 1, 0], camera, 0, 1080));
});

test('placements derive left/right and near/far from the camera, with per-subject facing', () => {
  const objects = [
    {...createStageObject('human', 'amor'), position: [-1, 0, 1]},
    {...createStageObject('human', 'fati'), position: [1, 0, -1], rotation: [0, Math.PI, 0]},
  ];
  const result = placements(objects, camera, 1920, 1080);
  assert.match(result.amor, /左.*前景.*朝向镜头/);
  assert.match(result.fati, /右.*后景.*背向镜头/);
  assert.match(placements([objects[0]], camera, 1920, 1080).amor, /中景/);
  assert.equal(Object.keys(placements([{...objects[0], subject_ref: ''}], camera, 1920, 1080)).length, 0);
});

test('five preset poses are deterministic, editable and transformed in metres', () => {
  const points = new Set();
  for (const pose of ['standing', 'sitting', 'walking', 'kneeling', 'reaching']) {
    const person = {...createStageObject('human', 'fati'), pose};
    const joints = poseWorldJoints(person);
    assert.deepEqual(joints, poseWorldJoints(person));
    assert.ok(joints.head.every(Number.isFinite));
    points.add(JSON.stringify(joints.wrist_left));
  }
  assert.equal(points.size, 5);
  const person = createStageObject('human', 'fati');
  const baseline = poseWorldJoints(person);
  const edited = poseWorldJoints({...person, joints: {shoulder_left: 1}});
  assert.notDeepEqual(edited.wrist_left, baseline.wrist_left);
  const translated = poseWorldJoints({...person, position: [2, 3, 4]});
  baseline.head.forEach((v, i) => close(translated.head[i], v + [2, 3, 4][i]));
});

test('pose export groups two people and marks hidden/clipped keypoints rather than inventing visibility', () => {
  const stage = defaultStage(); stage.camera = camera;
  stage.objects = [createStageObject('human', 'amor'), {...createStageObject('human', 'fati'), position: [0, 0, 8]}, createStageObject('box')];
  const pose = poseKeypoints(stage, 640, 360);
  assert.equal(pose.people.length, 2);
  assert.equal(pose.people[0].subject_ref, 'amor');
  assert.ok(pose.people[0].keypoints.some(p => p.visible));
  assert.ok(pose.people[1].keypoints.every(p => !p.visible && p.confidence === 0));
  assert.equal(pose.width, 640); assert.equal(pose.height, 360);
  assert.deepEqual(JSON.parse(JSON.stringify(stage)), stage);
});

test('normalised linear depth is near-white, far-black and bounded', () => {
  close(normalisedDepth(.1, .1, 10), 1);
  close(normalisedDepth(10, .1, 10), 0);
  close(normalisedDepth(5.05, .1, 10), .5);
  close(normalisedDepth(100, .1, 10), 0);
  close(normalisedDepth(-1, .1, 10), 1);
  assert.throws(() => normalisedDepth(1, 1, 1));
});

test('resolution and object construction are bounded and reject unsupported primitives', () => {
  assert.deepEqual(resolutionSize('1920x1080'), {width: 1920, height: 1080});
  assert.deepEqual(resolutionSize('1080x1920'), {width: 1080, height: 1920});
  for (const value of ['0x0', 'oops', '99999x1080']) assert.throws(() => resolutionSize(value));
  for (const kind of ['human', 'box', 'cylinder', 'plane']) assert.equal(createStageObject(kind).kind, kind);
  assert.throws(() => createStageObject('spaceship'));
});

test('schema-optional transforms and vector joint rotations can be reconstructed from files', () => {
  const minimal = {id: 'p1', kind: 'human', position: [0, 0, 0]};
  assert.ok(poseWorldJoints(minimal).head.every(Number.isFinite));
  const vectorJoints = {...minimal, joints: {shoulder_left: [.5, 0, 0]}};
  assert.ok(poseWorldJoints(vectorJoints).wrist_left.every(Number.isFinite));
});
