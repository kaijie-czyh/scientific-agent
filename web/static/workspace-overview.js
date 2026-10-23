/* Projections only: all records, versions and task states belong to the server. */
(() => {
    'use strict';
    const E=(tag,text,cls='')=>{const n=document.createElement(tag);n.textContent=text || '';n.className=cls;return n;};
    const btn=(text,fn)=>{const b=E('button',text,'btn btn-secondary btn-sm');b.type='button';b.onclick=fn;return b;};
    const labels={processing:'处理中',completed:'已完成',failed:'失败',interrupted:'已中断',running:'运行中',queued:'排队中',partial:'部分完成',cancelled:'已取消'};
    async function get(url){const r=await fetch((window.SRA_API_BASE||'')+url),d=await r.json();if(!r.ok)throw new Error(d.detail || `读取失败 ${r.status}`);return d;}
    function mount(host,kind,app){
        const pid=app.getRoute().projectId,root=E('section','','workspace-unified');host.append(root);
        const active=()=>root.isConnected && app.getRoute().projectId===pid;
        if(!pid){root.append(E('p','请先选择项目，或从新研究开始。查看此页不会创建项目。'));return;}
        let offset=0,busy=false,timer=null,signature='';
        const title={reports:'研究现状',plans:'研究方案',history:'历史材料',activities:'任务活动'}[kind];
        root.append(E('h2',title,'page-title'));
        const state=E('p','','muted'),list=E('div'),feedback=E('p','','ch-warning');
        const next=btn('加载更多',()=>load(true));next.hidden=true;
        root.append(state,btn('返回当前科研对话',()=>app.navigate({page:'dashboard'})),btn('刷新',()=>load()),feedback,list,next);
        async function preview(entry,body){
            body.replaceChildren(E('p','正在读取所选版本…'));
            try{const r=await fetch((window.SRA_API_BASE||'')+entry.url);if(!r.ok)throw new Error('该版本暂不可读取');const text=await r.text();if(!active())return;body.replaceChildren();await window.SRA_DOCUMENT.render(body,text);}
            catch(e){if(active())body.replaceChildren(E('p',e.message,'ch-warning'));}
        }
        function entryCard(entry){
            const card=E('article','','card');card.dataset.materialKey=entry.key;
            card.append(E('h3',entry.title,'card-title'),E('p',[entry.label,entry.version!=null?'版本 '+entry.version:'未标数字版本',entry.status_label,entry.created_at?new Date(entry.created_at).toLocaleString('zh-CN'):entry.saved_at?'保存于 '+new Date(entry.saved_at).toLocaleString('zh-CN'):''].filter(Boolean).join(' · '),'muted small'));
            const body=E('div'),details=E('details');details.append(E('summary','查看此版本'),body);details.ontoggle=()=>{if(details.open&&!body.childNodes.length)preview(entry,body);};card.append(details);
            if(entry.key.startsWith('plan:'))card.append(btn('审阅正式记录',()=>window.SRA_CONVERSATIONS.showPlan(pid,entry.key.slice(5))));
            if(entry.conversation_id)card.append(btn('返回来源对话',()=>app.navigate({projectId:pid,conversationId:entry.conversation_id,page:'dashboard'})));
            const download=E('a','下载此版本（MD）','btn btn-secondary btn-sm');download.href=(window.SRA_API_BASE||'')+entry.url;download.setAttribute('download','');card.append(download);return card;
        }
        async function load(append=false,poll=false){
            if(busy||!active())return;clearTimeout(timer);busy=true;if(!poll)feedback.textContent='正在读取已保存的材料与状态…';next.disabled=true;if(!append)offset=0;
            try{
                const cap=await get('/api/conversation-capabilities');if(!cap.workspace_read_model)throw new Error('当前后端尚未加载统一材料与任务视图，请更新后端后重试；不会自动重启或恢复任务。');
                const data=await get(`/api/projects/${encodeURIComponent(pid)}/${kind==='activities'?'activities':'workspace/materials?kind='+kind}${kind==='activities'?'?':'&'}limit=20&offset=${offset}`);if(!active())return;
                window.dispatchEvent(new CustomEvent('sra:workspace-loaded',{detail:{projectId:pid,workflow:data.workflow,counts:data.workflow.counts,material_counts:data.material_counts}}));
                state.textContent=(data.project_name || '当前项目')+' · 已确认进展：'+data.progress.label+'。'+data.progress.notice;
                const live=kind==='activities'&&data.activities.some(x=>['processing','running','queued'].includes(x.status));
                if(live)timer=setTimeout(()=>load(false,true),2000);
                const fresh=JSON.stringify(data);
                if(poll&&signature===fresh){offset=data.next_offset;feedback.textContent='';return;}
                signature=fresh;
                if(!append)list.replaceChildren();
                if(kind==='activities'){
                    for(const item of data.activities){
                        const card=E('article','','card');card.dataset.activityId=item.id;
                        card.append(E('h3',item.title,'card-title'),E('p',item.status_label||labels[item.status]||item.status),E('p',item.created_at?new Date(item.created_at).toLocaleString('zh-CN'):'' ,'muted small'));
                        if(item.error){const detail=E('details');detail.append(E('summary','查看失败原因'),E('p',item.error));card.append(detail);}
                        card.append(btn(['failed','interrupted'].includes(item.status)?'回到原对话核对并重试':'返回对应对话',()=>app.navigate({projectId:pid,conversationId:item.conversation_id,page:'dashboard'})));
                        if(item.result_url){const a=E('a','下载已保存报告','btn btn-secondary btn-sm');a.href=item.result_url;a.setAttribute('download','');card.append(a);}list.append(card);
                    }
                    const old=data.historical_execution;
                    if(!append && old && (old.node_id || old.restore_gate || old.status && old.status!=='created')){
                        const d=E('details');d.append(E('summary','旧流水线历史状态（与本轮对话任务分开）'),E('p',(old.status || '已有历史检查点')+'；只读查看不会重新启动任务。'),E('p',old.hint || '已有材料可查看和导出。'));
                        const cursor=E('details');cursor.append(E('summary','查看历史执行位置'),E('p',[old.stage,old.node_id].filter(Boolean).join(' · ')));d.append(cursor);
                        d.append(btn('查看历史产物',()=>mountHistory()));
                        if(old.action==='confirm')d.append(btn('查看待确认事项',()=>app.navigate({page:'human'})));
                        list.append(d);
                    }
                    if(!data.activities.length&&!append)list.append(E('p','尚无本项目对话请求或检索任务。已有材料仍可查看，不需要启动旧流水线。'));
                }else{
                    if(kind==='plans'&&!append){
                        const q=data.workflow.flow.question;list.append(E('p',q?'当前问题'+(q.needs_review?'（待复核）':'')+'：'+q.title:'尚未确认当前问题；可以从对话讨论或已有候选中明确采纳。'));
                    }
                    for(const entry of data.entries)list.append(entryCard(entry));
                    for(const draft of data.drafts || []){
                        const card=E('article','','card');card.dataset.suggestionId=draft.suggestion_id;card.append(E('h3',draft.title+' · 未采纳草案','card-title'),E('p',draft.basis_changed?'依据已变化或缺少快照，需复核，不能直接采纳。':'待用户审阅；尚未成为正式方案。'),btn('回原对话审阅草案',()=>window.SRA_CONVERSATIONS.showProposal(pid,draft.conversation_id,draft.suggestion_id)));list.append(card);
                    }
                    if(!data.entries.length&&!(data.drafts||[]).length&&!append)list.append(E('p',kind==='reports'?'尚无已保存的研究现状报告。入库论文与研究现状报告不是同一材料。':'尚无此类材料。不会为填满页面生成内容。','muted'));
                    if(kind==='plans'&&!append)list.append(btn('查看历史方法、实验记录与稿件',()=>mountHistory()));
                }
                const cursors=[data.next_offset,data.draft_next_offset].filter(n=>n!=null);offset=cursors.length?Math.min(...cursors):null;next.hidden=offset===null;feedback.textContent='';
            }catch(e){if(active())feedback.textContent=e.message;}finally{busy=false;next.disabled=false;}
        }
        function mountHistory(){root.remove();mount(host,'history',app);}
        load();
    }
    window.SRA_WORKSPACE_VIEW={mount};
})();
