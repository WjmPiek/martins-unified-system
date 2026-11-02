const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
test('the host accepts resize and retry messages only from its Attendance frame',()=>{
 let receive,reloads=0;
 const frame={contentWindow:{},dataset:{moduleOrigin:'https://attendance.example'},height:'640',scrollIntoView:()=>{}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../app/static/attendance-integrated.js'),'utf8'),{
  document:{getElementById:()=>frame},window:{addEventListener:(_,callback)=>receive=callback,location:{reload:()=>reloads++}}
 });
 const message={source:frame.contentWindow,origin:'https://attendance.example',data:{type:'martins:attendance-height',height:1200}};
 receive({...message,source:{}});assert.equal(frame.height,'640');
 receive({...message,origin:'https://evil.example'});assert.equal(frame.height,'640');
 receive(message);assert.equal(frame.height,'1200');
 receive({...message,data:{type:'martins:attendance-height',height:Infinity}});assert.equal(frame.height,'1200');
 receive({...message,data:{type:'martins:attendance-retry'}});assert.equal(reloads,1);
});
