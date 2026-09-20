// Minimal eventful DOM for console command tests; browser layout is checked separately.
const nodes = new Map();
class Element {
  constructor(tag) { this.tagName=tag.toUpperCase();this.children=[];this.listeners={};this.dataset={};this.attributes={};this.style={};this._value=null;this.hidden=false;this.disabled=false;this.checked=false;this.classList={toggle(){},add(){},remove(){}}; }
  set id(value) {this._id=value;nodes.set(value,this);} get id(){return this._id;}
  set value(value){this._value=String(value);} get value(){return this._value ?? (this.tagName==='SELECT' ? this.children[0]?.value || '' : '');}
  set innerHTML(value){this.children=[];for(const m of value.matchAll(/<([a-z0-9-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)){const el=new Element(m[1]);el.id=m[2];this.append(el);}}
  append(...values){for(const value of values){value.parent=this;this.children.push(value);}} prepend(value){value.parent=this;this.children.unshift(value);}
  replaceChildren(...values){this.children=[];this.append(...values);}
  setAttribute(key,value){this.attributes[key]=String(value);} getAttribute(key){return this.attributes[key]??null;}
  addEventListener(type,callback){(this.listeners[type]??=[]).push(callback);}
  async emit(type){for(const callback of this.listeners[type]||[])await callback({preventDefault(){},target:this});}
  focus(){document.activeElement=this;}
  contains(value){return value===this||this.children.some(child=>child.contains(value));}
  querySelectorAll(selector){const all=this.children.flatMap(child=>[child,...child.querySelectorAll(selector)]);return all.filter(child=>selector==='button'?child.tagName==='BUTTON':false);}
  reset(){for(const node of nodes.values())if(['INPUT','TEXTAREA'].includes(node.tagName))node.value='';}
}
const document={documentElement:{lang:'en'},visibilityState:'visible',activeElement:null,hasFocus:()=>true,
  getElementById:id=>nodes.get(id)||null,createElement:tag=>new Element(tag),createTextNode:text=>Object.assign(new Element('#text'),{textContent:text}),
  addEventListener(){},body:new Element('body')};
const mount=new Element('div');mount.id='task-changes-flow';
const listeners={};
const window={addEventListener(type,fn){(listeners[type]??=[]).push(fn);},dispatchEvent(event){for(const fn of listeners[event.type]||[])fn(event);},
  OrgRebaseWorkspaceShell:{navigate(){}},exactVmrcBinding:bundle=>bundle.minimal_rebase_certificate||null};
const CustomEvent=class{constructor(type,options={}){this.type=type;this.detail=options.detail;}};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const button=(text)=>{const candidates=mount.querySelectorAll('button').filter(item=>item.textContent===text);if(!candidates.length)throw new Error('Button missing: '+text);return candidates.at(-1);};
module.exports={document,window,CustomEvent,nodes,tick,button};
