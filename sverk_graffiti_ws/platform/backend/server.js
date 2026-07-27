app.post('/api/start-debate', (req, res) => {
    const prompt = req.body.prompt;
    // Публикуем команду с промптом
    redis.publish('sverk:agents:command', JSON.stringify({
        command: 'start_debate',
        prompt: prompt
    }));
    // Транслируем системное сообщение в чат
    redis.publish('sverk:agents:chat', JSON.stringify({
        agent: 'system',
        type: 'info',
        content: `Получен промпт: "${prompt}". Агенты начинают обсуждение.`
    }));
    res.json({status: 'debate_started'});
});