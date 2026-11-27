'use strict';
const fs=require('node:fs/promises');
async function login(target){
 const config=JSON.parse(await fs.readFile('/run/command-memory/dev-login.json','utf8'));
 const item=Object.values(config).find(p=>p.origin===target);
 if(!item)throw Error('No developer login has been enrolled for this application origin.');
 const endpoint=item.api || item.origin;
 const post=async(route,body)=>{const r=await fetch(endpoint+route,{method:'POST',redirect:'error',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(12000)});if(!r.ok)throw Error('Dev sign-in failed with HTTP '+r.status);return r.json()};
 if(item.kind==='coder'){
   const {session_token:token}=await post('/api/v2/users/login',{email:item.email,password:item.password});
   const headers={'Coder-Session-Token':token};
   const memberships=await fetch(endpoint+'/api/v2/users/me/organizations',{headers,redirect:'error'});if(!memberships.ok||!(await memberships.json()).some(org=>org.id===item.orgId))throw Error('Developer organization membership was not confirmed.');
   return {origin:item.origin,orgId:item.orgId,kind:item.kind,cookie:{name:'coder_session_token',value:token,httpOnly:true,secure:item.origin.startsWith('https:')}};
 }
 const result=await post('/api/auth/login',{email:item.email,password:item.password,orgId:item.orgId});
 if((result.orgId||result.org?.id)!==item.orgId || !result.token || result.platformRole==='PLATFORM_ADMIN')throw Error('Developer account organization mismatch.');
 return {origin:item.origin,kind:item.kind,orgId:item.orgId,storageKey:'bcentrisk_internal_token',token:result.token};
}
module.exports={login};
