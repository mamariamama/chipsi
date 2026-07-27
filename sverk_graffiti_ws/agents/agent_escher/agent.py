import os
import json
import time
import random
import redis
from typing import Dict, List


class ArtAgent:
    """Базовый агент для арт-дебатов"""

    def __init__(self, name: str, personality: str):
        self.name = name
        self.personality = personality

        # Подключение к Redis с повторными попытками
        redis_url = os.getenv('REDIS_URL', 'redis://localhost:6379')
        for attempt in range(10):
            try:
                self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
                self.redis.ping()
                print(f"Агент {self.name} подключился к Redis")
                break
            except redis.ConnectionError:
                print(f"Попытка {attempt + 1}/10 подключения к Redis...")
                time.sleep(2)
        else:
            raise Exception("Не удалось подключиться к Redis")

        # Параметры из .env
        self.max_rounds = int(os.getenv('MAX_DEBATE_ROUNDS', '10'))
        self.consensus_threshold = float(os.getenv('CONSENSUS_THRESHOLD', '0.75'))

        # Каналы
        self.chat_channel = 'sverk:agents:chat'
        self.consensus_channel = 'sverk:agents:consensus'
        self.command_channel = 'sverk:agents:command'

        # Состояние дебатов
        self.proposals = {}
        self.votes = {}
        self.my_proposal = None
        self.debate_round = 0
        self.debate_active = False
        self.current_prompt = None

        # Предложения по умолчанию (будут переопределены при получении промпта)
        self.default_proposals = self.get_default_proposals()
        self.art_proposals = self.default_proposals.copy()

        # Подписка на каналы
        self.pubsub = self.redis.pubsub()
        self.pubsub.subscribe(self.chat_channel, self.command_channel)

        print(f"Агент {self.name} ({self.personality}) готов к дебатам")
        print(f"Мои предложения по умолчанию: {self.default_proposals}")

    def get_default_proposals(self) -> List[str]:
        """Дефолтные предложения, если нет промпта"""
        raise NotImplementedError

    def generate_proposals_from_prompt(self, prompt: str) -> List[str]:
        """Генерация предложений на основе промпта"""
        raise NotImplementedError

    def generate_argument(self, proposal: str) -> str:
        """Генерация аргумента в стиле агента"""
        raise NotImplementedError

    def propose_art(self):
        """Предложить свой вариант рисунка"""
        if not self.art_proposals:
            self.art_proposals = self.default_proposals.copy()
        self.my_proposal = random.choice(self.art_proposals)

        message = {
            'agent': self.name,
            'type': 'proposal',
            'content': self.my_proposal,
            'argument': self.generate_argument(self.my_proposal),
            'confidence': round(random.uniform(0.7, 0.95), 2),
            'round': self.debate_round,
            'timestamp': time.time()
        }

        self.redis.publish(self.chat_channel, json.dumps(message))
        self.proposals[self.name] = self.my_proposal
        self.votes[self.my_proposal] = 1

        print(f"[{self.name}] Предлагаю: {self.my_proposal}")
        return self.my_proposal

    def debate(self, message: Dict):
        """Реакция на сообщения других агентов"""
        if message.get('agent') == self.name:
            return

        if message.get('type') == 'proposal':
            self.proposals[message['agent']] = message['content']
            self.votes[message['content']] = self.votes.get(message['content'], 0) + 1

            # Случайная реакция
            if random.random() < 0.4:  # 40% шанс поддержать
                self.support_proposal(message)
            else:
                self.criticize_proposal(message)

        elif message.get('type') == 'vote':
            proposal = message.get('content')
            if proposal:
                self.votes[proposal] = self.votes.get(proposal, 0) + 1
                self.check_consensus()

    def support_proposal(self, message: Dict):
        """Поддержать предложение другого агента"""
        response = {
            'agent': self.name,
            'type': 'support',
            'content': message['content'],
            'original_agent': message['agent'],
            'reason': f"Как {self.personality}, я ценю '{message['content']}' от {message['agent']}",
            'round': self.debate_round,
            'timestamp': time.time()
        }
        self.redis.publish(self.chat_channel, json.dumps(response))
        print(f"[{self.name}] Поддерживаю '{message['content']}' от {message['agent']}")

    def criticize_proposal(self, message: Dict):
        """Раскритиковать предложение"""
        counter = random.choice([p for p in self.art_proposals if p != message.get('content')])

        response = {
            'agent': self.name,
            'type': 'criticism',
            'content': message['content'],
            'original_agent': message['agent'],
            'counter_proposal': counter,
            'argument': f"'{message['content']}' недостаточно выразительно! '{counter}' лучше!",
            'round': self.debate_round,
            'timestamp': time.time()
        }
        self.redis.publish(self.chat_channel, json.dumps(response))
        print(f"[{self.name}] Критикую '{message['content']}', предлагаю '{counter}'")

    def check_consensus(self) -> bool:
        """Проверить достижение консенсуса"""
        if not self.votes:
            return False

        total_votes = sum(self.votes.values())
        if total_votes == 0:
            return False

        leader = max(self.votes.items(), key=lambda x: x[1])
        percentage = leader[1] / total_votes

        print(f"[{self.name}] Голоса: {self.votes}, лидер: {leader[0]} ({percentage:.0%})")

        if percentage >= self.consensus_threshold or self.debate_round >= self.max_rounds:
            final_decision = {
                'agent': 'system',
                'type': 'final_decision',
                'content': leader[0],
                'votes': self.votes,
                'rounds': self.debate_round,
                'forced': self.debate_round >= self.max_rounds,
                'timestamp': time.time()
            }
            self.redis.publish(self.chat_channel, json.dumps(final_decision))
            self.redis.publish(self.consensus_channel, json.dumps(final_decision))
            print(f"[{self.name}] КОНСЕНСУС: {leader[0]}")
            self.debate_active = False
            return True

        return False

    def run(self):
        """Основной цикл"""
        print(f"[{self.name}] Ожидание команды начала дебатов...")

        for message in self.pubsub.listen():
            if message['type'] != 'message':
                continue

            try:
                data = json.loads(message['data'])
            except json.JSONDecodeError:
                continue

            channel = message['channel']

            # Обработка команд
            if channel == self.command_channel:
                if data.get('command') == 'start_debate':
                    prompt = data.get('prompt', '')
                    self.current_prompt = prompt
                    print(f"[{self.name}] Получен промпт: '{prompt}'")

                    # Генерируем предложения на основе промпта
                    if prompt:
                        self.art_proposals = self.generate_proposals_from_prompt(prompt)
                    else:
                        self.art_proposals = self.default_proposals.copy()

                    print(f"[{self.name}] Новые предложения: {self.art_proposals}")

                    # Сброс состояния дебатов
                    self.debate_active = True
                    self.debate_round = 0
                    self.proposals = {}
                    self.votes = {}

                    # Задержка, чтобы все агенты успели подготовиться
                    time.sleep(random.uniform(0.5, 2.0))
                    self.propose_art()

            # Обработка сообщений в чате
            elif channel == self.chat_channel and self.debate_active:
                if data.get('agent') != self.name:
                    self.debate(data)

                    # Новый раунд, если видим увеличение номера раунда
                    if data.get('round', 0) > self.debate_round:
                        self.debate_round = data['round']
                        time.sleep(random.uniform(1.0, 3.0))

                        # Предложить снова, если нет консенсуса
                        if self.debate_active and self.debate_round < self.max_rounds:
                            self.propose_art()

                # Проверка консенсуса после каждого сообщения
                if self.debate_active:
                    self.check_consensus()


# ... (базовый класс ArtAgent как в первом файле)
# Специфичный агент Эшера

class EscherAgent(ArtAgent):
    """Агент в стиле Эшера - математическое искусство, парадоксы"""

    def __init__(self):
        super().__init__('escher', 'математический художник')

    def get_default_proposals(self):
        return [
            'Лестница Пенроуза',
            'Рисующие руки',
            'Мозаика ящериц',
            'Водопад',
            'Бесконечный узел'
        ]

    def generate_proposals_from_prompt(self, prompt: str) -> List[str]:
        prompt_lower = prompt.lower()
        proposals = []

        if 'парадокс' in prompt_lower or 'невозможн' in prompt_lower:
            proposals = ['Лестница Пенроуза', 'Водопад']
        elif 'мозаик' in prompt_lower or 'тесселяц' in prompt_lower:
            proposals = ['Мозаика ящериц']
        elif 'бесконечн' in prompt_lower or 'рекурс' in prompt_lower:
            proposals = ['Рисующие руки', 'Бесконечный узел']
        else:
            proposals = self.default_proposals.copy()

        return proposals if proposals else self.default_proposals.copy()

    def generate_argument(self, proposal):
        args = {
            'Лестница Пенроуза': 'Парадокс в чистом виде!',
            'Рисующие руки': 'Рекурсия бытия!',
            'Мозаика ящериц': 'Тесселяция пространства!',
            'Водопад': 'Вечный двигатель иллюзии!',
            'Бесконечный узел': 'Топологическая красота!'
        }
        return args.get(proposal, 'Математика — это искусство!')


if __name__ == '__main__':
    agent = EscherAgent()
    agent.run()