'use strict';

function pendingMain(state, conversationId) {
  return state.decisions.find(item=>!item.objective && item.conversationId===conversationId && item.status==='pending');
}

function retainedFlatFileReply(state) {
  const matches=state.direct.filter(item=>item.status==='queued' &&
    typeof item.prompt==='string' && item.prompt.trim().toLowerCase()==='no db these are just flat file json' &&
    /database_url|development database/i.test(pendingMain(state,item.conversationId)?.question || ''));
  return matches.length===1 ? matches[0] : undefined;
}

function nextDirect(state, runs) {
  if (runs.some(run => !run.objective) || state.direct.some(item => item.status === 'dispatching')) return undefined;
  const next = state.direct.find(item => item.status === 'queued');
  if (!next || state.decisions.some(decision => !decision.objective &&
      decision.conversationId === next.conversationId &&
      ['pending', 'answering', 'needs_review'].includes(decision.status))) return undefined;
  return next;
}

function nextLegacy(state) {
  const tasks = state.items.filter(item => item.objective);
  if (state.paused || tasks.some(item => ['running', 'waiting', 'failed'].includes(item.status))) return undefined;
  return tasks.find(item => item.status === 'queued');
}

function obsoleteMainQuestion(decision) {
  return !decision.objective && decision.status === 'pending' &&
    /\b(?:create\s+or\s+select|select\s+or\s+create)\s+(?:an?\s+)?(?:[a-z0-9-]+\s+){0,3}task\b/i.test(decision.question || '');
}

module.exports = {nextDirect, nextLegacy, obsoleteMainQuestion, pendingMain, retainedFlatFileReply};
