import torch
import torch.nn.functional as F
from torch.nn.utils.rnn import pad_sequence
from text.tokeniser import Tokeniser

def accuracy(output, target, topk=(1,)):
    pred = output.topk(max(topk), 1, True, True)[1].t()
    correct = pred.eq(target.view(1, -1).expand_as(pred))
    return [float(correct[:k].reshape(-1).float().sum(0, keepdim=True).cpu().numpy()) for k in topk]

def zeroshot_classifier(model, classnames, templates, language='en'):
    tokenizer = Tokeniser()
    device = model.device
    with torch.no_grad():
        zeroshot_weights = []
        for classname in classnames:
            texts = [torch.tensor(tokenizer.encode(template.format(classname), language)) for template in templates]
            texts = pad_sequence(texts).T.contiguous().to(device)
            class_embeddings = model.encode_text(texts)
            class_embedding = F.normalize(class_embeddings, dim=-1)
            zeroshot_weights.append(class_embedding)
        zeroshot_weights = torch.stack(zeroshot_weights, dim=1).to(device)
    return zeroshot_weights

def zeroshot_run(model, zeroshot_weights, dataloader):
    model.eval()
    with torch.no_grad():
        for batch in dataloader:
            text, mels, _, _ = batch
            audio_features = model.encode_audio(mels)
            audio_features = F.normalize(audio_features, dim=-1)
            text_temp, audio_temp = model.get_temps()

            logits = (audio_temp * audio_features @ zeroshot_weights)

            # measure accuracy
            acc1, acc5 = accuracy(logits, text, topk=(1, 5))
            top1 += acc1
            top5 += acc5
            n += mels.size(0)

    top1 = (top1 / n)
    top5 = (top5 / n)
    return top1, top5