const { chromium } = require('../../node_modules/playwright')
const { pathToFileURL } = require('node:url')
const path = require('node:path')
const fs = require('node:fs')
const assert = require('node:assert/strict')
;(async()=>{
  const browser=await chromium.launch({headless:true})
  const results=[]
  for(let index=1;index<=3;index+=1){
    const name=`dir-${index}`
    const url=pathToFileURL(path.resolve(`design-demos/vip-card-20261108/${name}.html`)).href
    for(const [mode,width,height] of [['desktop',1440,1000],['mobile',390,844]]){
      const context=await browser.newContext({viewport:{width,height},reducedMotion:'no-preference'})
      const page=await context.newPage()
      const errors=[]
      page.on('pageerror',error=>errors.push(error.message))
      await page.goto(url)
      await page.screenshot({path:`design-demos/vip-card-20261108/${name}-${mode}.png`,fullPage:true,animations:'disabled'})
      assert.equal(await page.locator('.bank').count(),6)
      await page.locator('.bank').nth(2).click()
      assert.equal(await page.locator('.bank').nth(2).getAttribute('aria-pressed'),'true')
      assert.equal(await page.locator('.bank[aria-pressed="true"]').count(),1)
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true,`${name} 横向溢出`)
      const small=await page.locator('button').evaluateAll(elements=>elements.flatMap(element=>{const r=element.getBoundingClientRect();return r.height<44?[{height:r.height}]:[]}))
      assert.deepEqual(small,[],`${name} 点击区域不足`)
      assert.deepEqual(errors,[],`${name} 控制台异常`)
      results.push({name,mode,cards:6,selection:true,overflow:false,smallTargets:small,errors})
      await context.close()
    }
  }
  await browser.close()
  fs.writeFileSync('design-demos/vip-card-20261108/render-results.json',JSON.stringify({passed:true,results},null,2))
  console.log(JSON.stringify({passed:true,results},null,2))
})().catch(error=>{console.error(error.stack);process.exitCode=1})
