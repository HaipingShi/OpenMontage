import * as THREE from 'three';
import {OrbitControls} from './vendor/three-0.181.2/OrbitControls.js';
import {TransformControls} from './vendor/three-0.181.2/TransformControls.js';
import {el} from './lib.js';
import {BONES, POSES, JOINT_NAMES, defaultStage, createStageObject, poseWorldJoints, poseKeypoints,
  fovDegrees, resolutionSize, placements} from './stage-math.mjs';

const KIND_LABELS = {human: '人体', box: '方块', cylinder: '圆柱', plane: '平面'};
const POSE_LABELS = {standing: '站立', sitting: '坐下', walking: '行走', kneeling: '跪下', reaching: '伸手'};
const JOINT_LABELS = {shoulder_left: '左肩', elbow_left: '左肘', shoulder_right: '右肩', elbow_right: '右肘', hip_left: '左髋', knee_left: '左膝', hip_right: '右髋', knee_right: '右膝'};
const clone = value => JSON.parse(JSON.stringify(value));

export class WorkbenchStage {
  constructor(container, onChange) {
    this.container = container;
    this.onChange = onChange;
    this.stage = defaultStage();
    this.resolution = '1920x1080';
    this.readOnly = true;
    this.selected = null;
    this.subjects = [];
    this.groups = new Map();
    this.toolbar = el('div', {class: 'stage-toolbar'});
    this.subjectChoice = el('select', {'aria-label': '舞台主体'});
    this.toolbar.append(this.subjectChoice);
    for (const kind of Object.keys(KIND_LABELS)) this.toolbar.append(el('button', {type: 'button', 'data-stage-write': '', onclick: () => this.addObject(kind)}, `添加${KIND_LABELS[kind]}`));
    this.toolbar.append(el('button', {type: 'button', onclick: () => this.setView(!this.cameraView)}, '切换相机预览'));
    this.toolbar.append(el('button', {type: 'button', 'data-stage-write': '', onclick: () => this.transform?.setMode('translate')}, '移动'));
    this.toolbar.append(el('button', {type: 'button', 'data-stage-write': '', onclick: () => this.transform?.setMode('rotate')}, '旋转'));
    this.objectChoice = el('select', {'aria-label': '舞台物体', onchange: () => this.select(this.objectChoice.value)});
    this.toolbar.append(this.objectChoice);
    this.canvas = el('canvas', {'aria-label': '三维舞台'});
    this.viewport = el('div', {class: 'stage-viewport'}, this.canvas);
    this.inspector = el('div', {class: 'stage-inspector'});
    this.cameraForm = el('details', {class: 'camera-editor'}, el('summary', {}, '相机参数（米 / 毫米）'));
    container.append(this.toolbar, this.viewport, this.inspector, this.cameraForm,
      el('p', {class: 'hint'}, '简化骨架用于构图，不含手指。深度图近白远黑；姿势格式为 OpenMontage joints v1。'));
    try {
      this.renderer = new THREE.WebGLRenderer({canvas: this.canvas, antialias: true, alpha: false});
      this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      this.renderer.outputColorSpace = THREE.SRGBColorSpace;
      this.scene = new THREE.Scene();
      this.scene.background = new THREE.Color('#151d29');
      this.scene.add(new THREE.HemisphereLight(0xffffff, 0x303540, 2));
      const light = new THREE.DirectionalLight(0xffffff, 2);
      light.position.set(3, 6, 4); this.scene.add(light);
      this.grid = new THREE.GridHelper(16, 16, 0x54647c, 0x303b50); this.scene.add(this.grid);
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(16, 16), new THREE.MeshStandardMaterial({color: 0x252c3a, side: THREE.DoubleSide}));
      floor.rotation.x = -Math.PI / 2; floor.position.y = -.012; this.scene.add(floor);
      this.shotCamera = new THREE.PerspectiveCamera();
      this.editorCamera = new THREE.PerspectiveCamera(50, 1, .1, 200);
      this.editorCamera.position.set(4, 3.5, 7);
      this.orbit = new OrbitControls(this.editorCamera, this.canvas);
      this.orbit.target.set(0, 1, 0); this.orbit.update();
      this.orbit.addEventListener('change', () => this.render());
      this.transform = new TransformControls(this.editorCamera, this.canvas);
      this.helper = this.transform.getHelper(); this.scene.add(this.helper);
      this.transform.addEventListener('dragging-changed', event => { this.orbit.enabled = !event.value && !this.cameraView; });
      this.transform.addEventListener('change', () => this.render());
      this.transform.addEventListener('objectChange', () => {
        if (this.readOnly || !this.selected) return;
        const object = this.stage.objects.find(item => item.id === this.selected);
        const group = this.groups.get(this.selected);
        object.position = group.position.toArray();
        object.rotation = [group.rotation.x, group.rotation.y, group.rotation.z];
        object.scale = group.scale.toArray(); this.changed(); this.drawInspector();
      });
      this.canvas.addEventListener('pointerup', event => {
        if (this.transform.dragging || this.transform.axis) return;
        const rect = this.canvas.getBoundingClientRect();
        const point = new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, 1 - (event.clientY - rect.top) / rect.height * 2);
        const ray = new THREE.Raycaster(); ray.setFromCamera(point, this.cameraView ? this.shotCamera : this.editorCamera);
        const hit = ray.intersectObjects([...this.groups.values()], true)[0];
        if (hit) this.select(hit.object.userData.stageId);
      });
      this.observer = new ResizeObserver(() => this.resize()); this.observer.observe(this.viewport);
      this.resize();
    } catch (error) {
      this.renderer = null;
      this.viewport.replaceChildren(el('p', {class: 'error'}, `无法启动三维舞台：${error.message}`));
    }
  }

  load(stage, resolution, subjects) {
    const source = clone(stage || defaultStage());
    this.stage = {...defaultStage(), ...source, camera: {...defaultStage().camera, ...source.camera},
      objects: source.objects.map(object => ({...createStageObject(object.kind, object.subject_ref, object.id), ...object}))};
    this.resolution = resolution || '1920x1080'; this.setSubjects(subjects || []);
    this.rebuild(); this.drawCamera();
  }

  setSubjects(subjects) {
    const previous = this.subjectChoice.value;
    this.subjects = subjects;
    this.subjectChoice.replaceChildren(el('option', {value: ''}, '新人物'), ...subjects.map(subject => el('option', {value: subject.ref_id}, subject.ref_id)));
    if (subjects.some(subject => subject.ref_id === previous)) this.subjectChoice.value = previous;
    else if (subjects.length) this.subjectChoice.value = subjects[0].ref_id;
  }

  setReadOnly(value) {
    this.readOnly = value;
    this.container.querySelectorAll('[data-stage-write]').forEach(node => { node.disabled = value; });
    if (this.transform) { this.transform.enabled = !value && !this.cameraView; if (value) this.transform.detach(); }
  }

  serialise() { return clone(this.stage); }
  changed() { this.onChange(this.serialise()); this.render(); }

  addObject(kind) {
    if (this.readOnly) return;
    if (this.stage.objects.length >= 64) return;
    const id = `o-${crypto.randomUUID()}`;
    const subjectRef = this.subjectChoice.value || (kind === 'human' ? `person-${this.stage.objects.filter(item => item.kind === 'human').length + 1}` : '');
    const object = createStageObject(kind, subjectRef, id);
    object.color = ['#d4a768', '#91b9ea', '#cc8cd0', '#90c8ae'][this.stage.objects.length % 4];
    object.position[0] = this.stage.objects.length * .8;
    this.stage.objects.push(object); this.rebuild(); this.select(id); this.changed();
  }

  disposeGroup(group) {
    group.traverse(item => { item.geometry?.dispose(); if (item.material) item.material.dispose(); });
    this.scene.remove(group);
  }

  rebuild() {
    this.objectChoice.replaceChildren(el('option', {value: ''}, '选择物体'), ...this.stage.objects.map(object => el('option', {value: object.id}, `${KIND_LABELS[object.kind]} · ${object.subject_ref || object.id.slice(-6)}`)));
    if (this.renderer) {
      this.transform.detach();
      for (const group of this.groups.values()) this.disposeGroup(group);
      this.groups.clear();
      for (const object of this.stage.objects) {
        const group = new THREE.Group(); group.name = object.id;
        const material = new THREE.MeshStandardMaterial({color: object.color, roughness: .8, side: THREE.DoubleSide});
        if (object.kind === 'human') {
          const joints = poseWorldJoints({...object, position: [0, 0, 0], rotation: [0, 0, 0], scale: [1, 1, 1]});
          for (const [name, point] of Object.entries(joints)) {
            const sphere = new THREE.Mesh(new THREE.SphereGeometry(name === 'head' ? .12 : .055, 12, 8), material.clone());
            sphere.position.fromArray(point); group.add(sphere);
          }
          for (const [start, end] of BONES) {
            const a = new THREE.Vector3(...joints[start]), b = new THREE.Vector3(...joints[end]);
            const direction = b.clone().sub(a);
            const bone = new THREE.Mesh(new THREE.CylinderGeometry(.045, .045, direction.length(), 8), material.clone());
            bone.position.copy(a.add(b).multiplyScalar(.5)); bone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.normalize()); group.add(bone);
          }
          material.dispose();
        } else {
          const geometry = object.kind === 'box' ? new THREE.BoxGeometry() : object.kind === 'cylinder' ? new THREE.CylinderGeometry(.5, .5, 1, 20) : new THREE.PlaneGeometry(2, 2);
          const mesh = new THREE.Mesh(geometry, material); mesh.position.y = object.kind === 'plane' ? 1 : .5; group.add(mesh);
        }
        group.position.fromArray(object.position); group.rotation.set(...object.rotation); group.scale.fromArray(object.scale);
        group.traverse(item => { item.userData.stageId = object.id; }); this.scene.add(group); this.groups.set(object.id, group);
      }
      this.updateCamera();
    }
    this.select(this.stage.objects.some(item => item.id === this.selected) ? this.selected : '');
  }

  select(id) {
    this.selected = id || null; this.objectChoice.value = id || '';
    if (this.transform) {
      this.transform.detach(); if (id && !this.readOnly && !this.cameraView) this.transform.attach(this.groups.get(id));
    }
    this.drawInspector(); this.render();
  }

  numberField(label, value, update, options = {}) {
    const input = el('input', {type: 'number', step: options.step || '.1', value, 'data-stage-write': '', 'aria-label': label});
    if (options.min != null) input.min = options.min;
    if (options.max != null) input.max = options.max;
    input.disabled = this.readOnly;
    input.addEventListener('change', () => {
      if (this.readOnly || !input.checkValidity() || !Number.isFinite(Number(input.value))) return;
      update(Number(input.value));
    });
    return el('label', {}, el('span', {}, label), input);
  }

  drawInspector() {
    this.inspector.replaceChildren();
    const object = this.stage.objects.find(item => item.id === this.selected);
    if (!object) { this.inspector.append(el('p', {class: 'hint'}, '选择物体后可调整位置、旋转与姿势。')); return; }
    const subject = el('select', {'data-stage-write': '', 'aria-label': '物体对应主体'}, el('option', {value: ''}, '未关联'), ...this.subjects.map(item => el('option', {value: item.ref_id}, item.ref_id)));
    subject.value = object.subject_ref; subject.disabled = this.readOnly;
    subject.addEventListener('change', () => { if (!this.readOnly) { object.subject_ref = subject.value; this.changed(); this.rebuild(); } });
    this.inspector.append(el('label', {}, '对应主体', subject));
    const color = el('input', {type: 'color', value: object.color, 'data-stage-write': '', 'aria-label': '物体颜色'}); color.disabled = this.readOnly;
    color.addEventListener('input', () => { if (!this.readOnly) { object.color = color.value; this.rebuild(); this.changed(); } });
    this.inspector.append(el('label', {}, '颜色', color));
    for (const [key, label] of [['position', '位置'], ['rotation', '旋转'], ['scale', '尺寸']]) {
      for (let i = 0; i < 3; i++) this.inspector.append(this.numberField(`${label} ${'XYZ'[i]}${key === 'rotation' ? '°' : '米'}`, key === 'rotation' ? object[key][i] * 180 / Math.PI : object[key][i], value => {
        object[key][i] = key === 'rotation' ? value * Math.PI / 180 : value; this.rebuild(); this.changed();
      }, key === 'scale' ? {min: .05, max: 30} : {}));
    }
    if (object.kind === 'human') {
      const pose = el('select', {'aria-label': '人体姿势', 'data-stage-write': ''}, ...POSES.map(name => el('option', {value: name}, POSE_LABELS[name])));
      pose.value = object.pose; pose.disabled = this.readOnly;
      pose.addEventListener('change', () => { if (!this.readOnly) { object.pose = pose.value; object.joints = {}; this.rebuild(); this.changed(); } });
      this.inspector.append(el('label', {}, '预设姿势', pose));
      for (const name of JOINT_NAMES) this.inspector.append(this.numberField(`${JOINT_LABELS[name]}角度°`, (Array.isArray(object.joints[name]) ? -object.joints[name][0] : object.joints[name] || 0) * 180 / Math.PI, value => {
        object.joints[name] = value * Math.PI / 180; this.rebuild(); this.changed();
      }, {step: 5, min: -180, max: 180}));
    }
    const remove = el('button', {type: 'button', 'data-stage-write': '', onclick: () => {
      if (this.readOnly) return;
      this.stage.objects = this.stage.objects.filter(item => item.id !== object.id); this.rebuild(); this.changed();
    }}, '移除此物体'); remove.disabled = this.readOnly; this.inspector.append(remove);
  }

  drawCamera() {
    this.cameraForm.querySelectorAll('label').forEach(node => node.remove());
    for (const [key, label] of [['position', '相机位置'], ['target', '相机目标']]) {
      for (let i = 0; i < 3; i++) this.cameraForm.append(this.numberField(`${label} ${'XYZ'[i]}`, this.stage.camera[key][i], value => { this.stage.camera[key][i] = value; this.updateCamera(); this.changed(); }));
    }
    for (const [key, label] of [['lens_mm', '焦段'], ['sensor_width_mm', '传感器宽'], ['sensor_height_mm', '传感器高'], ['near', '近裁切'], ['far', '远裁切']]) {
      this.cameraForm.append(this.numberField(label, this.stage.camera[key], value => { this.stage.camera[key] = value; this.updateCamera(); this.changed(); }, {min: .01}));
    }
  }

  updateCamera() {
    if (!this.renderer) return;
    const {width, height} = resolutionSize(this.resolution), source = this.stage.camera;
    this.shotCamera.aspect = width / height;
    this.shotCamera.fov = fovDegrees(source.lens_mm, source.sensor_width_mm, source.sensor_height_mm, width / height);
    this.shotCamera.near = source.near; this.shotCamera.far = source.far;
    this.shotCamera.position.fromArray(source.position); this.shotCamera.lookAt(...source.target);
    this.shotCamera.updateProjectionMatrix(); this.shotCamera.updateMatrixWorld(); this.render();
  }

  resize() {
    if (!this.renderer) return;
    const availableWidth = this.viewport.clientWidth || 640;
    const size = resolutionSize(this.resolution);
    const width = Math.min(availableWidth, 480 * size.width / size.height);
    const height = width * size.height / size.width;
    this.renderer.setSize(width, height, false); this.canvas.style.width = `${width}px`; this.canvas.style.height = `${height}px`;
    this.editorCamera.aspect = width / height; this.editorCamera.updateProjectionMatrix(); this.render();
  }

  setView(value) {
    this.cameraView = value; if (!this.renderer) return;
    this.orbit.enabled = !value; this.transform.enabled = !value && !this.readOnly;
    this.helper.visible = !value && !this.readOnly && !!this.selected;
    this.viewport.dataset.cameraView = String(value); this.render();
  }

  render() { if (this.renderer) this.renderer.render(this.scene, this.cameraView ? this.shotCamera : this.editorCamera); }

  async export() {
    if (this.readOnly) throw new Error('只读舞台不能导出写入');
    if (!this.renderer) throw new Error('三维舞台不可用');
    const {width, height} = resolutionSize(this.resolution);
    this.updateCamera(); this.scene.updateMatrixWorld(true);
    const canvas = document.createElement('canvas');
    const renderer = new THREE.WebGLRenderer({canvas, antialias: true, preserveDrawingBuffer: true});
    renderer.setPixelRatio(1); renderer.setSize(width, height, false); renderer.outputColorSpace = THREE.SRGBColorSpace;
    const previousHelper = this.helper.visible, previousGrid = this.grid.visible, background = this.scene.background;
    this.helper.visible = false;
    try {
      renderer.render(this.scene, this.shotCamera);
      const composition = canvas.toDataURL('image/png');
      const depthMaterial = new THREE.ShaderMaterial({uniforms: {near: {value: this.stage.camera.near}, far: {value: this.stage.camera.far}}, side: THREE.DoubleSide,
        vertexShader: 'varying float viewDepth; void main(){ vec4 p=modelViewMatrix*vec4(position,1.0); viewDepth=-p.z; gl_Position=projectionMatrix*p; }',
        fragmentShader: 'uniform float near; uniform float far; varying float viewDepth; void main(){ float d=clamp(1.0-(viewDepth-near)/(far-near),0.0,1.0); gl_FragColor=vec4(vec3(d),1.0); }'});
      this.grid.visible = false; this.scene.background = new THREE.Color(0x000000); this.scene.overrideMaterial = depthMaterial;
      renderer.outputColorSpace = THREE.LinearSRGBColorSpace; renderer.toneMapping = THREE.NoToneMapping;
      renderer.render(this.scene, this.shotCamera);
      const depth = canvas.toDataURL('image/png'); this.scene.overrideMaterial = null; depthMaterial.dispose();
      const pose = poseKeypoints(this.stage, width, height);
      // Visibility also checks occlusion against stage meshes, not only the camera frustum.
      const ray = new THREE.Raycaster(), cameraOrigin = this.shotCamera.position;
      for (const person of pose.people) {
        const object = this.stage.objects.find(item => item.id === person.object_id), joints = poseWorldJoints(object);
        for (const point of person.keypoints) {
          if (!point.visible) continue;
          const world = new THREE.Vector3(...joints[point.name]); const delta = world.clone().sub(cameraOrigin);
          ray.set(cameraOrigin, delta.clone().normalize());
          const hit = ray.intersectObjects([...this.groups.values()], true)[0];
          if (hit && hit.distance < delta.length() - .13) { point.visible = false; point.confidence = 0; }
        }
      }
      const poseCanvas = document.createElement('canvas'); poseCanvas.width = width; poseCanvas.height = height;
      const context = poseCanvas.getContext('2d'); context.fillStyle = '#000'; context.fillRect(0, 0, width, height);
      for (const person of pose.people) {
        const object = this.stage.objects.find(item => item.id === person.object_id);
        context.strokeStyle = object.color; context.fillStyle = object.color; context.lineWidth = Math.max(3, width / 300);
        const points = Object.fromEntries(person.keypoints.map(point => [point.name, point]));
        for (const [start, end] of BONES) {
          if (!points[start].visible || !points[end].visible) continue;
          context.beginPath(); context.moveTo(points[start].x, points[start].y); context.lineTo(points[end].x, points[end].y); context.stroke();
        }
        for (const point of person.keypoints.filter(point => point.visible)) { context.beginPath(); context.arc(point.x, point.y, Math.max(4, width / 200), 0, Math.PI * 2); context.fill(); }
      }
      return {stage: this.serialise(), images: {composition, depth, pose: poseCanvas.toDataURL('image/png')}, pose,
        placements: placements(this.stage.objects, this.stage.camera, width, height)};
    } finally {
      this.scene.overrideMaterial = null; this.scene.background = background; this.helper.visible = previousHelper; this.grid.visible = previousGrid;
      renderer.dispose(); this.render();
    }
  }
}
