import assert from 'node:assert/strict';
import handler from './api/tesla-callback.js';
function invoke(url, method = 'GET') {
  const response = {headers:{},statusCode:200, setHeader(k,v){this.headers[k]=v;},status(v){this.statusCode=v;return this;},send(v){this.body=v;return this;}};
  handler({url,method},response); return response;
}
const state = 'a'.repeat(64);
const accepted = invoke(`/auth/tesla/callback?state=${state}&code=one-time-code`);
assert.equal(accepted.statusCode,303);
assert.equal(new URL(accepted.headers.Location).protocol,'sparc:');
assert.equal(new URL(accepted.headers.Location).host,'tesla');
assert.match(accepted.headers['Cache-Control'],/no-store/);
assert.equal(accepted.headers['Referrer-Policy'],'no-referrer');
assert.equal(invoke(`/auth/tesla/callback?state=${state}&state=${state}&code=x`).statusCode,400);
assert.equal(invoke(`/auth/tesla/callback?state=${state}&code=a%0D%0Ab`).statusCode,400);
assert.equal(invoke('/auth/tesla/callback?state=wrong&code=x').statusCode,400);
assert.equal(invoke(`/auth/tesla/callback?state=${state}&code=${'x'.repeat(4097)}`).statusCode,400);
assert.equal(invoke(`/auth/tesla/callback?state=${state}&code=x`,'POST').statusCode,405);
assert.equal(new URL(invoke(`/auth/tesla/callback?state=${state}&error=access_denied&error_description=private`).headers.Location).searchParams.get('error'),'authorization_cancelled');
console.log('Callback validation and fixed app redirect checks passed.');
